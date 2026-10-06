"""One-shot eval harness for Phase 7 retrieval. Not committed as a durable
script — just the shape used to produce the written report (trigger-gate scan
over all 24 discussion games, then 5-6 sample retrievals across the triple-
overlap set with eyeball-call notes)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import distinct, select

from app.db import SessionLocal
from app.models import GameEvent, L2MCall, L2MReport, SocialDiscussion
from app.retrieval.retrieve import (
    build_query_from_game_event,
    build_query_from_l2m_call,
    channel_diversity,
    retrieve_for_play,
)
from app.retrieval.trigger import game_passes_trigger


def print_trigger_scan() -> None:
    with SessionLocal() as session:
        games = sorted(session.execute(
            select(distinct(SocialDiscussion.game_id))
        ).scalars())
    print(f"\n--- trigger-gate scan over {len(games)} discussion games "
          f"(>=50 positives AND >=7%, timestamped-only) ---")
    passes = 0
    for g in games:
        r = game_passes_trigger(g)
        print(f"  {r}")
        if r.passes:
            passes += 1
    print(f"  passes={passes}/{len(games)}")


def print_triple_overlap_games(session) -> list[str]:
    ge = set(session.execute(select(distinct(GameEvent.game_id))).scalars())
    sd = set(session.execute(select(distinct(SocialDiscussion.game_id))).scalars())
    l2m = set(session.execute(select(distinct(L2MReport.game_id))).scalars())
    overlap = sorted(ge & sd & l2m)
    print(f"\ntriple overlap (game_events ∩ social_discussion ∩ l2m_reports): "
          f"{len(overlap)}")
    for g in overlap:
        print(f"  {g}")
    return overlap


def print_retrieval(label: str, query_text: str, results, k: int = 5) -> None:
    print(f"\n=== {label} ===")
    print(f"query_text: {query_text!r}")
    if not results:
        print("  (no results)")
        return
    for i, r in enumerate(results[:k], 1):
        text = (r.comment_text[:220]
                + ("..." if len(r.comment_text) > 220 else "")).replace("\n", " ")
        print(f"  {i}. score={r.score:.3f}  triage={r.triage_prob:.3f}  "
              f"video={r.video_id}  channel={r.source_channel!r}")
        print(f"     {text!r}")
    div = channel_diversity(results[:k])
    print(f"  diversity  videos={div['videos']}")
    print(f"             channels={div['channels']}")


def pick_sample_plays(session) -> list[tuple[str, str, str]]:
    """Return (label, query_text, game_id) for 5-6 real plays:

      1. J.T. Orr technical on 0022500696 — explicit game_events selection
      2–3. IC/INC L2M calls (incorrect / incorrect-no-call) from the triple
         overlap, picked by call_rating.
      4–5. CNC/CC L2M calls from heavier-discussion games for contrast.
      6. One game_event foul from a non-GSW/DET triple-overlap game.
    """
    out: list[tuple[str, str, str]] = []

    # 1. J.T. Orr technical on 0022500696, by called_by_ref_id -> Referee.name
    jt_orr = session.execute(select(GameEvent)
        .where(GameEvent.game_id == "0022500696")
        .where(GameEvent.action_type == "Foul")
        .where(GameEvent.sub_type == "Technical")
        .order_by(GameEvent.action_number)
    ).scalars().all()
    # Pick the one that mentions Orr in the description (Draymond tech) if present.
    jt_event = next((e for e in jt_orr if "Orr" in (e.description or "")),
                    jt_orr[0] if jt_orr else None)
    if jt_event is not None:
        q = build_query_from_game_event(session, jt_event)
        out.append(("0022500696 — J.T. Orr technical (game_events)", q, "0022500696"))

    # 2–3. IC + INC L2M calls, from triple-overlap games.
    overlap_games = list(session.execute(
        select(distinct(L2MReport.game_id))
    ).scalars())
    disc_games = set(session.execute(
        select(distinct(SocialDiscussion.game_id))
    ).scalars())
    overlap_games = [g for g in overlap_games if g in disc_games]

    for rating in ("IC", "INC"):
        row = session.execute(
            select(L2MCall)
            .join(L2MReport, L2MReport.id == L2MCall.l2m_report_id)
            .where(L2MReport.game_id.in_(overlap_games))
            .where(L2MCall.call_rating == rating)
            .order_by(L2MCall.id)
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            gid = session.execute(
                select(L2MReport.game_id)
                .where(L2MReport.id == row.l2m_report_id)
            ).scalar_one()
            q = build_query_from_l2m_call(session, row)
            out.append((f"{gid} — L2M call rated {rating}", q, gid))

    # 4. A CC call (confirmed correct) from the heaviest-discussion game that
    # isn't 0022500696, to see whether retrieval picks up discussion of a
    # correctly-called play.
    row = session.execute(
        select(L2MCall)
        .join(L2MReport, L2MReport.id == L2MCall.l2m_report_id)
        .where(L2MReport.game_id.in_(overlap_games))
        .where(L2MReport.game_id != "0022500696")
        .where(L2MCall.call_rating == "CC")
        .order_by(L2MCall.id)
        .limit(1)
    ).scalar_one_or_none()
    if row is not None:
        gid = session.execute(select(L2MReport.game_id)
                              .where(L2MReport.id == row.l2m_report_id)).scalar_one()
        q = build_query_from_l2m_call(session, row)
        out.append((f"{gid} — L2M call rated CC", q, gid))

    # 5. game_events-level play from a different triple-overlap game — pick
    # the first flagrant/technical we find to probe a "controversial-looking"
    # event even without an L2M rating.
    other_evt = session.execute(
        select(GameEvent)
        .where(GameEvent.game_id.in_(
            [g for g in overlap_games if g != "0022500696"]
        ))
        .where(GameEvent.sub_type.in_(
            ("Technical", "Flagrant Type 1", "Flagrant Type 2")
        ))
        .order_by(GameEvent.game_id, GameEvent.action_number)
        .limit(1)
    ).scalar_one_or_none()
    if other_evt is not None:
        q = build_query_from_game_event(session, other_evt)
        out.append((f"{other_evt.game_id} — technical/flagrant (game_events)",
                    q, other_evt.game_id))

    # 6. One generic shooting-foul CNC call from an overlap game to contrast
    # against the IC/INC/technical cases.
    generic = session.execute(
        select(L2MCall)
        .join(L2MReport, L2MReport.id == L2MCall.l2m_report_id)
        .where(L2MReport.game_id.in_(overlap_games))
        .where(L2MCall.call_rating == "CNC")
        .where(L2MCall.call_type.like("Foul: Shooting"))
        .order_by(L2MCall.id)
        .limit(1)
    ).scalar_one_or_none()
    if generic is not None:
        gid = session.execute(select(L2MReport.game_id)
                              .where(L2MReport.id == generic.l2m_report_id)).scalar_one()
        q = build_query_from_l2m_call(session, generic)
        out.append((f"{gid} — L2M shooting-foul CNC", q, gid))

    return out


def main() -> int:
    print_trigger_scan()

    with SessionLocal() as session:
        print_triple_overlap_games(session)
        for label, query, gid in pick_sample_plays(session):
            results = retrieve_for_play(session, gid, query, k=5)
            print_retrieval(label, query, results, k=5)

    return 0


if __name__ == "__main__":
    sys.exit(main())
