"""
One-off diagnostic: does YouTube (recap/analysis videos + their comment
threads) have enough per-game officiating-tied discussion volume to serve as
the Phase 6 fan-discussion source? See spec > Fan Discussion Sourcing.

NOT wired into the ingestion pipeline. Companion to check_bluesky_volume.py.

For a query + date range, finds recap/analysis videos via search.list
(publishedAfter / publishedBefore), then pulls up to N pages of top-level
comments per video via commentThreads.list. Prints per-video comment
totals, a text sample per video, and a rough quota-cost tally.

Auth uses YOUTUBE_API_KEY from backend/.env, loaded via the same Settings
pattern as JWT_SECRET / DATABASE_URL (app/config.py).

Run from backend/:
    python scripts/check_youtube_volume.py \
        --query "Draymond Green referee JT Orr" \
        --published-after 2026-01-31T00:00:00Z \
        --published-before 2026-02-07T00:00:00Z
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"

# Rough quota costs per the YouTube Data API v3 pricing table.
# search.list = 100 units/call, videos.list = 1, commentThreads.list = 1.
# Comment retrieval is intentionally cheap; the 10k/day cap is really a
# cap on how many search.list calls you make.
QUOTA_SEARCH = 100
QUOTA_VIDEOS = 1
QUOTA_COMMENTS = 1

MAX_COMMENT_PAGES = 10  # 100 comments/page * 10 = 1k top-level comments/video max


def _http_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} from {url}\n{body}", file=sys.stderr)
        raise


def search_videos(
    api_key: str,
    query: str,
    published_after: str,
    published_before: str,
    max_results: int,
) -> list[dict]:
    """Return up to max_results relevance-ranked video hits in the window."""
    params = {
        "key": api_key,
        "part": "snippet",
        "type": "video",
        "q": query,
        "publishedAfter": published_after,
        "publishedBefore": published_before,
        "maxResults": str(min(max_results, 50)),
        "order": "relevance",
    }
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    data = _http_json(url)
    return data.get("items", [])


def fetch_video_stats(api_key: str, video_ids: list[str]) -> dict[str, dict]:
    """Batch-fetch statistics (viewCount, commentCount) for the given ids."""
    if not video_ids:
        return {}
    params = {
        "key": api_key,
        "part": "statistics,snippet",
        "id": ",".join(video_ids),
    }
    url = VIDEOS_URL + "?" + urllib.parse.urlencode(params)
    data = _http_json(url)
    return {item["id"]: item for item in data.get("items", [])}


def fetch_comments(
    api_key: str, video_id: str, max_pages: int = MAX_COMMENT_PAGES
) -> tuple[list[dict], int, bool]:
    """Pull up to max_pages of top-level comments. Returns (comments, pages, disabled)."""
    comments: list[dict] = []
    page_token: str | None = None
    pages = 0
    for _ in range(max_pages):
        params = {
            "key": api_key,
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
            data = _http_json(url)
        except urllib.error.HTTPError as e:
            # 403 with commentsDisabled = channel has comments off. Signal it.
            if e.code == 403:
                return comments, pages, True
            raise
        pages += 1
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            comments.append({
                "text": top.get("textDisplay", ""),
                "author": top.get("authorDisplayName", "?"),
                "likes": top.get("likeCount", 0),
                "published": top.get("publishedAt", ""),
                "reply_count": item["snippet"].get("totalReplyCount", 0),
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.15)
    return comments, pages, False


def print_video_block(
    idx: int, video_id: str, meta: dict, comments: list[dict],
    pages: int, disabled: bool, sample_n: int = 5,
) -> None:
    snip = meta.get("snippet", {})
    stats = meta.get("statistics", {})
    title = snip.get("title", "?")
    channel = snip.get("channelTitle", "?")
    published = snip.get("publishedAt", "?")
    reported = stats.get("commentCount", "?")
    views = stats.get("viewCount", "?")

    print()
    print("-" * 80)
    print(f"[{idx}] {title}")
    print(f"    channel={channel!r}  published={published}  "
          f"views={views}  reported_comments={reported}")
    print(f"    videoId={video_id}  https://youtu.be/{video_id}")
    if disabled:
        print("    (comments disabled on this video)")
        return
    print(f"    fetched {len(comments)} top-level comments across {pages} page(s)"
          f" [MAX_COMMENT_PAGES={MAX_COMMENT_PAGES}]")

    if not comments:
        return
    top = sorted(comments, key=lambda c: c["likes"], reverse=True)[:sample_n]
    for i, c in enumerate(top, 1):
        text = c["text"].replace("\n", " ")
        if len(text) > 180:
            text = text[:177] + "..."
        print(f"      [{i}] likes={c['likes']:<5} replies={c['reply_count']:<4} "
              f"@{c['author']}  {c['published']}")
        print(f"          {text}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True,
                    help='search.list q (e.g. "Draymond Green referee JT Orr")')
    ap.add_argument("--published-after", required=True,
                    help="ISO8601 UTC, e.g. 2026-01-31T00:00:00Z")
    ap.add_argument("--published-before", required=True,
                    help="ISO8601 UTC, e.g. 2026-02-07T00:00:00Z")
    ap.add_argument("--max-videos", type=int, default=5,
                    help="how many top videos to pull comments for (default 5)")
    args = ap.parse_args()

    if not settings.youtube_api_key:
        print(
            "ERROR: YOUTUBE_API_KEY must be set in backend/.env "
            "(see .env.example).",
            file=sys.stderr,
        )
        return 2

    print(f'Searching: q={args.query!r}  '
          f'published in [{args.published_after} .. {args.published_before})')
    hits = search_videos(
        settings.youtube_api_key,
        args.query,
        args.published_after,
        args.published_before,
        max_results=args.max_videos,
    )
    print(f"search.list returned {len(hits)} video(s)")
    if not hits:
        print()
        print("=" * 80)
        print("NO VIDEOS FOUND for this query + window.")
        print("=" * 80)
        print(f"Approx quota used: {QUOTA_SEARCH}")
        return 0

    video_ids = [h["id"]["videoId"] for h in hits]
    stats_by_id = fetch_video_stats(settings.youtube_api_key, video_ids)

    total_fetched = 0
    total_reported = 0
    for i, vid_id in enumerate(video_ids, 1):
        meta = stats_by_id.get(vid_id, hits[i - 1])
        comments, pages, disabled = fetch_comments(
            settings.youtube_api_key, vid_id
        )
        print_video_block(i, vid_id, meta, comments, pages, disabled)
        total_fetched += len(comments)
        try:
            total_reported += int(meta.get("statistics", {}).get("commentCount", 0))
        except (TypeError, ValueError):
            pass

    quota = QUOTA_SEARCH + QUOTA_VIDEOS + len(video_ids) * QUOTA_COMMENTS * MAX_COMMENT_PAGES
    print()
    print("=" * 80)
    print(f"TOTAL top-level comments fetched: {total_fetched}")
    print(f"TOTAL reported comments (per video statistics.commentCount): "
          f"{total_reported}")
    print(f"Approx quota used (worst-case): {quota} / 10000 daily")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
