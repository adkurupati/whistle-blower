"""
Batch-ingest game_events for every game that matters for the AI Verdict engine.

Target set: all game_ids that have EITHER rows in `social_discussion` OR a row
in `l2m_reports`. Any game with those is in-scope for Phase 7 validation (the
AI Verdict engine either reads fan discussion for it or has official ground
truth to validate against — or both).

By default, games that already have game_events rows are skipped (idempotent
re-run safe). Pass --force to re-fetch them anyway; the UNIQUE(game_id,
action_number) constraint still de-dupes at insert time.

Same shape as ingest_month.py / ingest_youtube_batch.py: sequential, per-game
try/except with rollback, per-game commit so partial progress survives a
mid-run failure, ~0.6s pacing between games.

Run from backend/:
    python scripts/ingest_game_events_batch.py
    python scripts/ingest_game_events_batch.py --force
    python scripts/ingest_game_events_batch.py --limit 10        # smoke test
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import distinct, select, union

from app.db import SessionLocal
from app.ingestion.play_by_play import API_SLEEP_SEC, ingest_game_events
from app.models import GameEvent, L2MReport, SocialDiscussion


def find_target_game_ids(session) -> list[str]:
    """Union of game_ids with social_discussion rows OR an l2m_reports row."""
    sd = select(distinct(SocialDiscussion.game_id).label("game_id"))
    l2m = select(distinct(L2MReport.game_id).label("game_id"))
    rows = session.execute(union(sd, l2m).order_by("game_id")).all()
    return [r[0] for r in rows]


def already_ingested_game_ids(session) -> set[str]:
    return set(session.execute(select(distinct(GameEvent.game_id))).scalars())


def print_progress(
    idx: int, total: int, gid: str, t_start: float, failures: int,
    outcome: str,
) -> None:
    elapsed = time.time() - t_start
    per_game = elapsed / idx if idx else 0
    eta = per_game * (total - idx)
    print(
        f"[{idx:>3}/{total}] {gid}  {outcome}  "
        f"elapsed={elapsed:>5.0f}s  eta={eta:>5.0f}s  fails={failures}",
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--force", action="store_true",
        help="re-ingest games that already have game_events rows "
             "(default: skip them)",
    )
    ap.add_argument(
        "--limit", type=int, default=None,
        help="only process the first N target games (smoke-test helper)",
    )
    args = ap.parse_args()

    # Quieter default — ingest_game_events logs every unmatched/ambiguous row
    # at INFO, which is overwhelming at batch scale. Per-game aggregate stats
    # are printed by this script directly, and the sample lists are collected
    # into the final report.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    per_game: list[dict] = []
    failures: list[tuple[str, str]] = []
    skipped_already: list[str] = []
    # Aggregated unmatched/ambiguous descriptions across the whole batch for
    # the final analysis — small enough to keep in memory given ~85 games.
    all_unmatched: list[tuple[str, int, str]] = []  # (game_id, action, desc)
    all_ambiguous: list[tuple[str, int, str]] = []

    t_start = time.time()

    with SessionLocal() as session:
        all_targets = find_target_game_ids(session)
        targets = all_targets
        if not args.force:
            done = already_ingested_game_ids(session)
            skipped_already = [g for g in targets if g in done]
            targets = [g for g in targets if g not in done]

        if args.limit is not None:
            targets = targets[: args.limit]

        total = len(targets)
        print(
            f"target games (social_discussion ∪ l2m_reports): "
            f"{len(all_targets)}  "
            f"already-ingested skipped: {len(skipped_already)}  "
            f"to ingest: {total}",
            flush=True,
        )
        if total == 0:
            print("Nothing to do.", flush=True)
            return 0

        for i, gid in enumerate(targets, 1):
            try:
                stats = ingest_game_events(session, gid)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                failures.append((gid, f"{type(exc).__name__}: {exc}"))
                print_progress(i, total, gid, t_start, len(failures),
                               f"FAIL {type(exc).__name__}: {exc}")
                time.sleep(API_SLEEP_SEC)
                continue
            session.commit()
            per_game.append({
                "game_id": gid,
                "foul_rows": stats.foul_rows,
                "matched": stats.matched,
                "unmatched": stats.unmatched,
                "ambiguous": stats.ambiguous,
                "inserted": stats.inserted,
                "skipped": stats.skipped,
            })
            for an, desc in stats.unmatched_samples:
                all_unmatched.append((gid, an, desc))
            for an, desc in stats.ambiguous_samples:
                all_ambiguous.append((gid, an, desc))
            outcome = (
                f"fouls={stats.foul_rows:>3} matched={stats.matched:>3} "
                f"unmatched={stats.unmatched:>2} ambiguous={stats.ambiguous:>2} "
                f"ins={stats.inserted:>3} skip={stats.skipped:>3}"
            )
            print_progress(i, total, gid, t_start, len(failures), outcome)
            if i < total:
                time.sleep(API_SLEEP_SEC)

    # ---------- final summary ----------
    elapsed = time.time() - t_start
    attempted = len(per_game) + len(failures)
    succeeded = len(per_game)
    totals = {
        k: sum(r[k] for r in per_game)
        for k in ("foul_rows", "matched", "unmatched", "ambiguous",
                  "inserted", "skipped")
    }
    print("\n" + "=" * 78)
    print(f"BATCH SUMMARY  attempted={attempted}  succeeded={succeeded}  "
          f"failed={len(failures)}  skipped_already_ingested={len(skipped_already)}  "
          f"elapsed={elapsed:.0f}s")
    print("=" * 78)
    print(
        f"totals  foul_rows={totals['foul_rows']}  "
        f"matched={totals['matched']}  unmatched={totals['unmatched']}  "
        f"ambiguous={totals['ambiguous']}  "
        f"(inserted={totals['inserted']}, skipped={totals['skipped']})"
    )
    if totals["foul_rows"]:
        rate = totals["matched"] / totals["foul_rows"] * 100
        print(f"overall ref-match rate: {rate:.2f}% "
              f"({totals['matched']}/{totals['foul_rows']})")

    if failures:
        print("\nFailures:")
        for gid, err in failures:
            print(f"  {gid}  -> {err}")

    if all_unmatched:
        print(f"\nUnmatched samples ({len(all_unmatched)} total, up to 20 per game):")
        for gid, an, desc in all_unmatched:
            print(f"  {gid} action={an}  {desc!r}")
    if all_ambiguous:
        print(f"\nAmbiguous samples ({len(all_ambiguous)} total, up to 20 per game):")
        for gid, an, desc in all_ambiguous:
            print(f"  {gid} action={an}  {desc!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
