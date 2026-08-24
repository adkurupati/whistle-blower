"""
Batch-ingest YouTube fan discussion for a set of games.

Two selection modes (mutually exclusive):
    --date YYYY-MM-DD     — every game in `games` on that date
    --game-ids ID [ID...] — an explicit list, useful when the games span
                            multiple dates (e.g. a hand-picked training set)

Same pattern as ingest_month.py: sequential, per-game try/except with
rollback so one bad game doesn't kill the batch, per-game commit so partial
progress survives a mid-run failure, and idempotent via
UNIQUE(source, source_item_id) + ON CONFLICT DO NOTHING inside
persist_comments() (so re-running is safe).

Run from backend/:
    python scripts/ingest_youtube_batch.py --date 2026-01-30
    python scripts/ingest_youtube_batch.py --game-ids 0022400230 0022400169 ...
"""

import argparse
import sys
import time
from datetime import date as date_type, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
from app.ingestion.youtube import YouTubeIngestor, persist_comments
from app.models import Game


def find_game_ids_on(session, target: date_type) -> list[str]:
    """Return game_ids for the given date, ordered by id."""
    return list(session.execute(
        select(Game.id).where(Game.date == target).order_by(Game.id)
    ).scalars())


def print_progress(
    idx: int, total: int, gid: str, t_start: float,
    inserted: int, skipped: int, fetched: int, failures: int,
) -> None:
    elapsed = time.time() - t_start
    per_game = elapsed / idx if idx else 0
    eta = per_game * (total - idx)
    print(
        f"[{idx:>2}/{total}] {gid}  "
        f"fetched={fetched:>5}  inserted={inserted:>5}  skipped={skipped:>5}  "
        f"elapsed={elapsed:>5.0f}s  eta={eta:>4.0f}s  fails={failures}",
        flush=True,
    )


def _validate_game_ids(session, game_ids: list[str]) -> list[str]:
    """Order the caller's list by game_id and warn on any not in `games`."""
    known = set(session.execute(
        select(Game.id).where(Game.id.in_(game_ids))
    ).scalars())
    missing = [g for g in game_ids if g not in known]
    if missing:
        print(f"WARNING: {len(missing)} game_id(s) not in `games`, skipping: "
              f"{missing}", flush=True)
    return sorted(g for g in game_ids if g in known)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", help="YYYY-MM-DD (US game date)")
    mode.add_argument("--game-ids", nargs="+", metavar="ID",
                      help="explicit list, e.g. --game-ids 0022400230 0022400169")
    args = ap.parse_args()

    per_game_results: list[dict] = []
    failures: list[tuple[str, str]] = []
    t_start = time.time()
    # Populated below depending on which mode ran — used in the summary header.
    selection_label: str

    with SessionLocal() as session:
        if args.date is not None:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
            game_ids = find_game_ids_on(session, target)
            selection_label = f"date={target}"
            if not game_ids:
                print(f"No games in `games` on {target} — nothing to ingest.")
                return 0
            print(f"Found {len(game_ids)} game(s) on {target}", flush=True)
        else:
            game_ids = _validate_game_ids(session, list(args.game_ids))
            selection_label = f"game_ids=[{len(game_ids)} explicit]"
            if not game_ids:
                print("No valid game_ids after validation — nothing to ingest.")
                return 0
            print(f"Ingesting {len(game_ids)} explicit game(s)", flush=True)

        total = len(game_ids)

        # One ingestor reuses one session; per-game commit/rollback happens
        # inside the loop.
        ingestor = YouTubeIngestor(session)

        for i, gid in enumerate(game_ids, 1):
            try:
                comments = ingestor.fetch_for_game(gid)
                inserted, skipped = persist_comments(session, gid, comments)
                session.commit()
                per_game_results.append({
                    "game_id": gid,
                    "fetched": len(comments),
                    "inserted": inserted,
                    "skipped": skipped,
                })
                print_progress(i, total, gid, t_start,
                               inserted, skipped, len(comments), len(failures))
            except Exception as exc:
                session.rollback()
                failures.append((gid, f"{type(exc).__name__}: {exc}"))
                print(f"    ! FAIL {gid}: {type(exc).__name__}: {exc}", flush=True)
                print_progress(i, total, gid, t_start,
                               0, 0, 0, len(failures))

    # ---------- final summary ----------
    print("\n" + "=" * 78)
    print(f"BATCH SUMMARY  {selection_label}  games={total}  "
          f"failures={len(failures)}  elapsed={time.time() - t_start:.0f}s")
    print("=" * 78)
    print(f"{'game_id':<12}  {'fetched':>7}  {'inserted':>8}  {'skipped':>7}")
    print("-" * 78)
    for r in per_game_results:
        print(f"{r['game_id']:<12}  {r['fetched']:>7}  "
              f"{r['inserted']:>8}  {r['skipped']:>7}")

    totals = {
        k: sum(r[k] for r in per_game_results)
        for k in ("fetched", "inserted", "skipped")
    }
    print("-" * 78)
    print(f"{'TOTALS':<12}  {totals['fetched']:>7}  "
          f"{totals['inserted']:>8}  {totals['skipped']:>7}")

    if failures:
        print("\nFailures:")
        for gid, err in failures:
            print(f"  {gid}  -> {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
