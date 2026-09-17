"""
Phase 7 calibration: what counts as a discussion "spike" worth running the
AI Verdict engine on, vs. normal per-team chatter?

Needs real `published_at` data (see the 2026-09-17 fix in
app/ingestion/youtube.py / migration b3f6a1c9d2e4) -- rows ingested before
that fix have published_at = NULL and are skipped here, not treated as
zero-volume.

Approach: per game, bucket comments into hourly bins relative to that game's
own earliest comment timestamp (a proxy for "when post-game content started
appearing", since we don't have exact video-publish times pre-aggregated).
Print raw volume per bin, plus each bin's ratio to that game's own median
hourly rate -- deliberately per-game-relative, not a fixed cross-game count,
because a blowout thread and a one-possession game have wildly different
baseline volume (see spec's Fan Discussion Sourcing / AI Verdict Engine
notes on this).

Run from backend/, after re-ingesting at least one controversial and one
routine game with the published_at fix in place:
    python scripts/analyze_discussion_spikes.py 0022500696 0022400020
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import SocialDiscussion


def load_timestamps(session, game_id: str) -> list:
    rows = session.execute(
        select(SocialDiscussion.published_at)
        .where(SocialDiscussion.game_id == game_id)
        .where(SocialDiscussion.published_at.is_not(None))
    ).scalars().all()
    return sorted(rows)


def bucket_hourly(timestamps: list) -> dict[int, int]:
    """hour-index (0 = hour containing the earliest comment) -> comment count."""
    if not timestamps:
        return {}
    t0 = timestamps[0]
    buckets: dict[int, int] = defaultdict(int)
    for ts in timestamps:
        hour_index = int((ts - t0).total_seconds() // 3600)
        buckets[hour_index] += 1
    return dict(buckets)


def report(game_id: str, timestamps: list) -> None:
    print(f"\n{'=' * 60}")
    print(f"game_id={game_id}  ({len(timestamps)} comments with published_at)")
    print("=" * 60)
    if not timestamps:
        print("  (no timestamped rows -- re-ingest this game after the fix)")
        return

    buckets = bucket_hourly(timestamps)
    rates = list(buckets.values())
    med = median(rates) if rates else 0
    print(f"  span: {timestamps[0]} -> {timestamps[-1]}")
    print(f"  hourly median: {med:.1f} comments/hr across {len(buckets)} active hours")
    print(f"\n  {'hour':>5}  {'count':>6}  {'x median':>9}")
    for h in sorted(buckets):
        count = buckets[h]
        ratio = count / med if med else float("inf")
        flag = "  <-- spike?" if med and ratio >= 3 else ""
        print(f"  {h:>5}  {count:>6}  {ratio:>8.1f}x{flag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("game_ids", nargs="+", help="e.g. 0022500696 0022400020")
    args = ap.parse_args()

    session = SessionLocal()
    try:
        for game_id in args.game_ids:
            timestamps = load_timestamps(session, game_id)
            report(game_id, timestamps)
    finally:
        session.close()

    print(
        "\nNote: '>=3x median' is a starting guess, not a calibrated threshold "
        "-- eyeball whether it actually separates the controversial game's "
        "real spike from the routine game's flat curve before treating it "
        "as final. Update this script's flag logic once real numbers are in."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
