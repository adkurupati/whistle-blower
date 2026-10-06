"""
Phase 7 retrieval: Qdrant semantic search over social_discussion.

Pipeline per flagged play:
  1. Build a natural-language query string from the play (an l2m_calls row
     or a game_events row). See build_query_from_l2m_call /
     build_query_from_game_event.
  2. Embed the query with the same all-MiniLM-L6-v2 model used at index time.
  3. Semantic search in Qdrant filtered on game_id AND is_relevant=True
     (triage classifier already flagged these as about officiating).
  4. Diversity cap: at most MAX_PER_VIDEO results from any single video_id,
     so one long-tail reaction video can't dominate the top-k. Nulls (non-
     YouTube rows) share one bucket.

What this layer deliberately does NOT do:
  - Infer fanbase / team allegiance from comment text or channel. YouTube
    comments carry no team attribution, and guessing one would launder a
    model guess into the bias-mitigation signal the AI Verdict engine wants
    to be *measuring*. Downstream code gets what the data actually has
    (video_id, source_channel, engagement, timestamp) and can report channel
    diversity as a weaker proxy — see sample_retrieval.py for the write-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from qdrant_client.http import models as qm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.triage_inference import embed_query
from app.models import GameEvent, L2MCall, Player, Referee, SocialDiscussion
from app.retrieval.qdrant import COLLECTION, get_client


MAX_PER_VIDEO = 3  # per-video diversity cap on the final top-k
OVERFETCH_FACTOR = 5  # pull k*OVERFETCH_FACTOR before capping to leave room


@dataclass(frozen=True)
class RetrievedComment:
    social_discussion_id: int
    score: float            # cosine similarity (higher = closer)
    comment_text: str
    game_id: str
    video_id: str | None
    source_channel: str | None
    published_at: str | None
    engagement_score: int | None
    triage_prob: float
    retrieval_query_template: str | None


# ---------- retrieval ----------

def retrieve_for_play(
    session: Session,
    game_id: str,
    query_text: str,
    k: int = 10,
    max_per_video: int = MAX_PER_VIDEO,
) -> list[RetrievedComment]:
    """Top-k comments for one play. Filters on game_id + is_relevant=True.

    `session` is only used to hydrate comment_text (Qdrant payload keeps the
    search-relevant metadata but not the full comment body, which stays in
    Postgres so there's one source of truth for comment content).
    """
    client = get_client()
    qvec = embed_query(query_text)

    overfetch = max(k * OVERFETCH_FACTOR, k)
    hits = client.query_points(
        collection_name=COLLECTION,
        query=qvec.tolist(),
        limit=overfetch,
        with_payload=True,
        query_filter=qm.Filter(must=[
            qm.FieldCondition(key="game_id", match=qm.MatchValue(value=game_id)),
            qm.FieldCondition(key="is_relevant", match=qm.MatchValue(value=True)),
        ]),
    ).points

    if not hits:
        return []

    # Hydrate comment_text from Postgres in one shot.
    ids = [int(h.id) for h in hits]
    texts = dict(session.execute(
        select(SocialDiscussion.id, SocialDiscussion.comment_text)
        .where(SocialDiscussion.id.in_(ids))
    ).all())

    results: list[RetrievedComment] = []
    per_video: dict[str | None, int] = {}
    for h in hits:  # already sorted by score descending
        vid = h.payload.get("video_id")
        if per_video.get(vid, 0) >= max_per_video:
            continue
        per_video[vid] = per_video.get(vid, 0) + 1
        results.append(RetrievedComment(
            social_discussion_id=int(h.id),
            score=float(h.score),
            comment_text=texts.get(int(h.id), ""),
            game_id=h.payload.get("game_id"),
            video_id=vid,
            source_channel=h.payload.get("source_channel"),
            published_at=h.payload.get("published_at"),
            engagement_score=h.payload.get("engagement_score"),
            triage_prob=float(h.payload.get("triage_prob", 0.0)),
            retrieval_query_template=h.payload.get("retrieval_query_template"),
        ))
        if len(results) >= k:
            break
    return results


# ---------- query builders ----------

def _player_name(session: Session, player_id: int | None) -> str | None:
    if player_id is None:
        return None
    return session.execute(
        select(Player.name).where(Player.id == player_id)
    ).scalar_one_or_none()


def _ref_name(session: Session, ref_id: int | None) -> str | None:
    if ref_id is None:
        return None
    return session.execute(
        select(Referee.name).where(Referee.id == ref_id)
    ).scalar_one_or_none()


def build_query_from_l2m_call(session: Session, call: L2MCall) -> str:
    """E.g. 'Q4 00:30.1 Foul: Personal — LeBron James fouled Jalen Williams. '
    'NBA review: James ... (ruling: INC)'. Fed to the embedder as-is."""
    parts: list[str] = [f"{call.period} {call.pc_time} {call.call_type}"]

    # Raw names already come as readable strings on the l2m_calls row (and may
    # legitimately BE team names, per the schema note), so use them directly
    # rather than re-resolving via player_id joins.
    cp = call.committing_player_name
    dp = call.disadvantaged_player_name
    if cp or dp:
        parts.append(f"{cp} on {dp}")

    if call.nba_comment:
        parts.append(f"NBA review: {call.nba_comment}")

    if call.call_rating:
        parts.append(f"ruling: {call.call_rating}")

    return " — ".join(parts)


def build_query_from_game_event(session: Session, event: GameEvent) -> str:
    """E.g. 'Period 4 clock PT00M48.00S Foul (Technical) — called by J.T. Orr'
    + description. Reads the calling ref name via called_by_ref_id when set."""
    parts: list[str] = [
        f"Period {event.period} clock {event.clock}",
        event.action_type + (f" ({event.sub_type})" if event.sub_type else ""),
    ]

    ref_name = _ref_name(session, event.called_by_ref_id)
    if ref_name:
        parts.append(f"called by {ref_name}")

    if event.description:
        parts.append(event.description)

    return " — ".join(parts)


# ---------- small convenience ----------

def channel_diversity(results: Iterable[RetrievedComment]) -> dict:
    """Per-video and per-channel counts across a result set.

    Reported alongside sample retrievals as the weaker-but-honest proxy for
    the AI Verdict engine's cross-fanbase bias-mitigation signal — YouTube
    comments carry no team attribution so a real fanbase split can't be read
    off this data (flagged clearly in the sample retrieval printouts).
    """
    videos: dict[str | None, int] = {}
    channels: dict[str | None, int] = {}
    for r in results:
        videos[r.video_id] = videos.get(r.video_id, 0) + 1
        channels[r.source_channel] = channels.get(r.source_channel, 0) + 1
    return {"videos": videos, "channels": channels}
