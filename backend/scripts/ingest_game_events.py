"""
Ingest game_events (foul rows from PlayByPlayV3) for one or more games.

Idempotent via UNIQUE(game_id, action_number) + ON CONFLICT DO NOTHING;
re-runs are safe. ~0.6s pacing between games, per-game commit so partial
progress survives a mid-run failure.

Run from backend/:
    python scripts/ingest_game_events.py 0022500696
    python scripts/ingest_game_events.py 0022400138 0022400139 0022500696
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.ingestion.play_by_play import API_SLEEP_SEC, ingest_game_events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("game_ids", nargs="+", help="one or more game_ids, e.g. 0022500696")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    totals = {"foul_rows": 0, "matched": 0, "unmatched": 0, "ambiguous": 0,
              "inserted": 0, "skipped": 0}

    with SessionLocal() as session:
        for i, gid in enumerate(args.game_ids):
            try:
                stats = ingest_game_events(session, gid)
            except Exception as e:  # noqa: BLE001
                session.rollback()
                print(f"{gid}: FAILED — {e!r}", file=sys.stderr)
                continue
            session.commit()
            print(
                f"{gid}: fouls={stats.foul_rows:>3} matched={stats.matched:>3} "
                f"unmatched={stats.unmatched:>2} ambiguous={stats.ambiguous:>2} "
                f"(inserted={stats.inserted}, skipped={stats.skipped})"
            )
            for k in totals:
                totals[k] += getattr(stats, k)
            if i < len(args.game_ids) - 1:
                time.sleep(API_SLEEP_SEC)

    print(
        f"\ntotals across {len(args.game_ids)} game(s): "
        f"fouls={totals['foul_rows']} matched={totals['matched']} "
        f"unmatched={totals['unmatched']} ambiguous={totals['ambiguous']} "
        f"(inserted={totals['inserted']}, skipped={totals['skipped']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
