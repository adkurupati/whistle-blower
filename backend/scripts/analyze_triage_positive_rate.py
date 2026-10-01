"""
Phase 7 calibration: does the triage classifier's positive rate cleanly
separate a known-controversial game from a routine one at the game level?

Companion to analyze_discussion_spikes.py -- that script's per-game hourly
'>=3x median' spike rule did NOT cleanly separate the JT Orr / GSW@DET game
(0022500696) from a routine game (0022400020), because the routine game's
one first-day burst produced a higher median-multiple than the controversial
game's multi-day cascade. Positive-classifier-volume was tried as an
alternative and it did separate them (~14% vs ~5% at threshold 0.4).

Runs the Phase 6 triage classifier (app/ml/triage_inference) over every
comment in social_discussion with a non-NULL published_at for each supplied
game_id, and prints total / positives(>=threshold) / positive-rate per game.

Run from backend/:
    python scripts/analyze_triage_positive_rate.py 0022500696 0022400020
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
from app.ml.triage_inference import DEFAULT_THRESHOLD, classify_comments
from app.models import SocialDiscussion


def load_comment_texts(session, game_id: str) -> list[str]:
    return session.execute(
        select(SocialDiscussion.comment_text)
        .where(SocialDiscussion.game_id == game_id)
        .where(SocialDiscussion.published_at.is_not(None))
    ).scalars().all()


def report(game_id: str, texts: list[str], threshold: float) -> None:
    print(f"\n{'=' * 60}")
    print(f"game_id={game_id}  ({len(texts)} comments with published_at)")
    print("=" * 60)
    if not texts:
        print("  (no timestamped rows -- re-ingest this game after the "
              "published_at backfill fix)")
        return

    results = classify_comments(list(texts), threshold=threshold)
    positives = sum(1 for r in results if r.is_relevant)
    rate = 100.0 * positives / len(texts)
    print(f"  threshold:      {threshold}")
    print(f"  total:          {len(texts)}")
    print(f"  positives:      {positives}")
    print(f"  positive rate:  {rate:.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("game_ids", nargs="+", help="e.g. 0022500696 0022400020")
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"probability >= threshold counts as positive (default {DEFAULT_THRESHOLD})",
    )
    args = ap.parse_args()

    session = SessionLocal()
    try:
        for game_id in args.game_ids:
            texts = load_comment_texts(session, game_id)
            report(game_id, texts, args.threshold)
    finally:
        session.close()

    print(
        "\nNote: positive-rate is a per-game aggregate signal; it separated "
        "0022500696 (controversial) from 0022400020 (routine) at threshold "
        "0.4 in the 2026-09-17 calibration (14.12% vs 4.63%). Re-check when "
        "the classifier is retrained or the threshold moves."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
