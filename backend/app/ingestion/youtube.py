"""
Fetch-only YouTube fan-discussion ingestor for the AI Verdict engine.

Given a game_id, looks up the game's date + both team names, runs a small
set of query template variants against YouTube Data API v3's search.list to
find controversy/recap/analysis videos in the 1-7 day post-game window, then
pulls top-level commentThreads.list off each unique video found.

Returns a plain list of dicts. Does NOT touch the database beyond reading
the game row, does NOT write to social_discussion, does NOT touch Qdrant.
A separate step (next task) will wire this fetcher into a per-row upsert
against social_discussion using its UNIQUE(source, source_item_id) constraint.

Design decisions worth remembering:
- Multi-template search, not one query: check_youtube_volume.py proved that
  "Draymond Green tech referee" (broad) surfaced Speakeasy/Whitlock/Sporting
  News with 844 fetched comments, while "Draymond Green referee JT Orr"
  (specific) got 15. Same underlying question, 55x volume gap purely from
  phrasing. Production ingestion has to sweep templates, not commit to one.
- No `requests`, urllib only: matches the rest of the backend and keeps
  the dependency footprint small.
- Video de-dup by videoId across templates so a video that matches three
  queries only gets its comments pulled once.
- Fetch-only, no persistence: the persistence layer wants the raw comment
  id (for source_item_id) and every returned dict already carries it, but
  actually writing rows and running ON CONFLICT DO NOTHING is the next step,
  not this one.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from app.config import settings
from app.models import Game, SocialDiscussion, Team

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"


@dataclass(frozen=True)
class _GameContext:
    game_id: str
    game_date: date
    home_name: str
    away_name: str


class YouTubeIngestor:
    # Learned from the check_youtube_volume.py spot-check: mix broad and
    # specific phrasings so we catch both mainstream debate channels
    # (broad hits like "Warriors referee") and dedicated recap/reaction
    # videos (specific hits like "Warriors Pistons recap"). Extend/override
    # by passing query_templates to __init__.
    DEFAULT_QUERY_TEMPLATES: tuple[str, ...] = (
        "{away} {home} referee",
        "{away} {home} recap",
        "{away} vs {home} technical foul",
        "{away} referee controversy",
        "{home} referee controversy",
    )

    def __init__(
        self,
        session: Session,
        api_key: str | None = None,
        query_templates: tuple[str, ...] | list[str] | None = None,
        videos_per_query: int = 5,
        max_comment_pages: int = 10,
        post_game_window_days: int = 7,
    ):
        self.session = session
        self.api_key = api_key or settings.youtube_api_key
        if not self.api_key:
            raise RuntimeError(
                "YOUTUBE_API_KEY not set in backend/.env — see .env.example"
            )
        self.query_templates = tuple(query_templates or self.DEFAULT_QUERY_TEMPLATES)
        self.videos_per_query = videos_per_query
        self.max_comment_pages = max_comment_pages
        self.post_game_window_days = post_game_window_days

    # ---------- public ----------

    def fetch_for_game(self, game_id: str) -> list[dict]:
        ctx = self._lookup_game(game_id)
        published_after, published_before = self._build_window(ctx.game_date)
        queries = self._render_queries(ctx)

        # videoId -> {"meta": search_hit, "query": first_template_that_found_it}
        video_map: dict[str, dict] = {}
        for q in queries:
            hits = self._search_videos(q, published_after, published_before)
            for h in hits:
                vid = h["id"]["videoId"]
                if vid not in video_map:
                    video_map[vid] = {"meta": h, "query": q}

        if not video_map:
            return []

        stats_by_id = self._fetch_video_stats(list(video_map.keys()))

        results: list[dict] = []
        for vid, entry in video_map.items():
            meta = stats_by_id.get(vid, entry["meta"])
            snip = meta.get("snippet", {}) or {}
            channel = snip.get("channelTitle", "")
            video_title = snip.get("title", "")
            comments = self._fetch_comments(vid)
            for c in comments:
                results.append({
                    "comment_id": c["comment_id"],
                    "comment_text": c["text"],
                    "video_id": vid,
                    "video_title": video_title,
                    "channel_name": channel,
                    "like_count": c["likes"],
                    "reply_count": c["reply_count"],
                    "published_at": c["published_at"],
                    "query_template": entry["query"],
                })
        return results

    # ---------- game lookup ----------

    def _lookup_game(self, game_id: str) -> _GameContext:
        HomeTeam = aliased(Team)
        AwayTeam = aliased(Team)
        row = self.session.execute(
            select(Game.id, Game.date, HomeTeam.name, AwayTeam.name)
            .join(HomeTeam, HomeTeam.id == Game.home_team_id)
            .join(AwayTeam, AwayTeam.id == Game.away_team_id)
            .where(Game.id == game_id)
        ).one_or_none()
        if row is None:
            raise LookupError(
                f"game_id={game_id!r} not found in `games` — ingest it first "
                f"(e.g. backend/scripts/ingest_one_day.py)"
            )
        gid, gdate, home_name, away_name = row
        return _GameContext(
            game_id=gid, game_date=gdate, home_name=home_name, away_name=away_name
        )

    def _build_window(self, game_date: date) -> tuple[str, str]:
        start_dt = datetime.combine(game_date, dtime.min, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=self.post_game_window_days)
        # YouTube expects RFC3339: "YYYY-MM-DDThh:mm:ssZ"
        return (
            start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def _render_queries(self, ctx: _GameContext) -> list[str]:
        substitutions = {"home": ctx.home_name, "away": ctx.away_name}
        return [t.format(**substitutions) for t in self.query_templates]

    # ---------- HTTP ----------

    @staticmethod
    def _http_json(url: str) -> dict:
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"HTTP {e.code} from {url}\n{body}", file=sys.stderr)
            raise

    def _search_videos(
        self, query: str, published_after: str, published_before: str
    ) -> list[dict]:
        params = {
            "key": self.api_key,
            "part": "snippet",
            "type": "video",
            "q": query,
            "publishedAfter": published_after,
            "publishedBefore": published_before,
            "maxResults": str(min(self.videos_per_query, 50)),
            "order": "relevance",
        }
        url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
        return self._http_json(url).get("items", [])

    def _fetch_video_stats(self, video_ids: list[str]) -> dict[str, dict]:
        if not video_ids:
            return {}
        params = {
            "key": self.api_key,
            "part": "statistics,snippet",
            "id": ",".join(video_ids),
        }
        url = VIDEOS_URL + "?" + urllib.parse.urlencode(params)
        data = self._http_json(url)
        return {item["id"]: item for item in data.get("items", [])}

    def _fetch_comments(self, video_id: str) -> list[dict]:
        """Top-level comments only. Returns [] silently if comments are disabled."""
        comments: list[dict] = []
        page_token: str | None = None
        for _ in range(self.max_comment_pages):
            params = {
                "key": self.api_key,
                "part": "snippet",
                "videoId": video_id,
                "maxResults": "100",
                "order": "relevance",
                "textFormat": "plainText",
            }
            if page_token:
                params["pageToken"] = page_token
            url = COMMENT_THREADS_URL + "?" + urllib.parse.urlencode(params)
            try:
                data = self._http_json(url)
            except urllib.error.HTTPError as e:
                # 403 = comments disabled (or, rarely, quota). Treat as
                # "no comments for this video" rather than aborting the run.
                if e.code == 403:
                    return comments
                raise
            for item in data.get("items", []):
                top = item["snippet"]["topLevelComment"]
                snip = top["snippet"]
                comments.append({
                    "comment_id": top["id"],
                    "text": snip.get("textDisplay", ""),
                    "likes": snip.get("likeCount", 0),
                    "published_at": snip.get("publishedAt", ""),
                    "reply_count": item["snippet"].get("totalReplyCount", 0),
                })
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            time.sleep(0.15)
        return comments


# ---------- persistence ----------
#
# Deliberately a plain function, not a method on YouTubeIngestor — the
# ingestor stays fetch-only. Caller is responsible for committing.

def _parse_youtube_timestamp(raw: str | None) -> datetime | None:
    """YouTube returns RFC3339 UTC, e.g. '2026-01-31T04:12:33Z'.

    datetime.fromisoformat doesn't accept a trailing 'Z' before Python 3.11's
    relaxed parser -- swap for '+00:00' so this works regardless of the
    interpreter running it. Returns None for missing/malformed input rather
    than raising, since a bad timestamp shouldn't sink the whole ingest run.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def persist_comments(
    session: Session, game_id: str, comments: list[dict]
) -> tuple[int, int]:
    """Bulk-upsert fetched comments into social_discussion.

    Returns (inserted_count, skipped_count). Skipped means the row already
    existed under the UNIQUE(source, source_item_id) constraint — this is
    how re-running the ingest for the same game stays idempotent.

    approx_game_clock and window_start/window_end are intentionally left
    NULL: the spec's Fan Discussion Sourcing / AI Verdict Engine sections
    both flag per-comment timestamp-to-game-clock alignment as still-open,
    and YouTube comments post hours-to-days after the game anyway, so
    inventing a value here would be worse than leaving it null.

    published_at (the comment's real post time, from the API's
    snippet.publishedAt) IS stored, as of this fix -- it was being fetched
    all along (see YouTubeIngestor._fetch_comments) but silently dropped
    here instead of written, which meant there was no real per-comment
    timing data to calibrate a discussion-"spike" threshold against.
    Discovered while scoping that Phase 7 work.
    """
    if not comments:
        return 0, 0

    rows = [
        {
            "game_id": game_id,
            "source": "youtube",
            "source_item_id": c["comment_id"],
            "comment_text": c["comment_text"],
            "source_channel": c["channel_name"],
            "video_id": c["video_id"],
            "retrieval_query_template": c["query_template"],
            "engagement_score": c["like_count"],
            "published_at": _parse_youtube_timestamp(c["published_at"]),
        }
        for c in comments
    ]
    stmt = (
        pg_insert(SocialDiscussion)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["source", "source_item_id"])
        .returning(SocialDiscussion.id)
    )
    inserted_ids = session.execute(stmt).scalars().all()
    inserted = len(inserted_ids)
    return inserted, len(rows) - inserted


# ---------- throwaway CLI demo ----------
#
# `python -m app.ingestion.youtube <game_id>` from backend/, or run this file
# directly. Prints a summary of what fetch_for_game returned so real output
# can be eyeballed before wiring to the DB.

def _demo(game_id: str) -> None:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        ing = YouTubeIngestor(session)
        results = ing.fetch_for_game(game_id)
    finally:
        session.close()

    if not results:
        print(f"No comments fetched for game_id={game_id!r}")
        return

    print(f"\nFetched {len(results)} comments for game_id={game_id!r}")

    # Per-video summary
    by_video: dict[str, dict] = {}
    for r in results:
        v = by_video.setdefault(r["video_id"], {
            "title": r["video_title"],
            "channel": r["channel_name"],
            "query": r["query_template"],
            "count": 0,
            "top_like": 0,
        })
        v["count"] += 1
        if r["like_count"] > v["top_like"]:
            v["top_like"] = r["like_count"]

    print("\n--- per-video ---")
    for vid, v in sorted(by_video.items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {v['count']:>4} comments  top-like={v['top_like']:<5} "
              f"[{v['channel']}] {v['title'][:60]}")
        print(f"       via q={v['query']!r}  https://youtu.be/{vid}")

    # Per-template surface
    print("\n--- per-template videos surfaced ---")
    by_template: dict[str, set[str]] = {}
    for r in results:
        by_template.setdefault(r["query_template"], set()).add(r["video_id"])
    for tpl, vids in by_template.items():
        print(f"  {len(vids):>3} unique video(s)  q={tpl!r}")

    # Top comment sample across the whole result
    print("\n--- top 10 comments by likes ---")
    for i, r in enumerate(sorted(results, key=lambda x: -x["like_count"])[:10], 1):
        text = r["comment_text"].replace("\n", " ")
        if len(text) > 160:
            text = text[:157] + "..."
        print(f"  [{i:2d}] likes={r['like_count']:<5} "
              f"replies={r['reply_count']:<4} [{r['channel_name']}]")
        print(f"       {text}")


if __name__ == "__main__":
    # Path setup so `python app/ingestion/youtube.py <id>` works from backend/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    if len(sys.argv) != 2:
        print(
            "usage: python -m app.ingestion.youtube <game_id>\n"
            "  e.g. python -m app.ingestion.youtube 0022500XXX  "
            "# Warriors @ Pistons 2026-01-30",
            file=sys.stderr,
        )
        sys.exit(2)
    _demo(sys.argv[1])
