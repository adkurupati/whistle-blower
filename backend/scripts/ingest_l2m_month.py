"""
Ingest L2M reports for every game in a month. Reuses ingest() from
ingest_l2m_one_game and find_games_in_month() from ingest_month.

The L2M endpoint 404s for games that didn't qualify for L2M coverage
(score wasn't within 3 in the last 2 min). We treat that as normal and
count it separately from real errors.

Same 0.6s pacing and per-game commit pattern as the other ingestion.

Run from backend/:
    python scripts/ingest_l2m_month.py
"""

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import SessionLocal
from ingest_l2m_one_game import ingest as ingest_l2m
from ingest_month import find_games_in_month

TARGET_YEAR = 2024
TARGET_MONTH = 11
API_SLEEP_SEC = 0.6


def main():
    pairs = find_games_in_month(TARGET_YEAR, TARGET_MONTH)
    total = len(pairs)
    print(f"checking {total} games for L2M reports ...", flush=True)

    had_report = 0
    no_report = 0
    errors = 0
    total_calls = 0
    rating_counts: Counter[str] = Counter()
    t_start = time.time()

    with SessionLocal() as session:
        for i, (gdate, gid) in enumerate(pairs, 1):
            try:
                result = ingest_l2m(session, gid)
                if result is None:
                    no_report += 1
                    outcome = "no L2M"
                else:
                    _report, paired, _meta = result
                    session.commit()
                    had_report += 1
                    total_calls += len(paired)
                    for _, call in paired:
                        rating_counts[call.call_rating or "null"] += 1
                    outcome = f"{len(paired)} calls"
            except Exception as exc:
                session.rollback()
                errors += 1
                outcome = f"ERROR {type(exc).__name__}: {exc}"

            elapsed = time.time() - t_start
            eta = (elapsed / i) * (total - i)
            print(
                f"[{i:>3}/{total}] {gdate} {gid}  → {outcome}  "
                f"(elapsed={elapsed:.0f}s eta={eta:.0f}s)",
                flush=True,
            )
            time.sleep(API_SLEEP_SEC)

    elapsed = time.time() - t_start
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  total games checked:  {total}")
    print(f"  had L2M report:       {had_report}")
    print(f"  no L2M report (404):  {no_report}")
    print(f"  errors:               {errors}")
    print(f"  total l2m_calls rows: {total_calls}")
    print(f"  elapsed:              {elapsed:.0f}s")
    print()
    print("Call rating distribution (this run):")
    for rating in ("CC", "CNC", "IC", "INC", "null"):
        print(f"  {rating:>4}  {rating_counts.get(rating, 0)}")


if __name__ == "__main__":
    main()
