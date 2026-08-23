"""
Ingest YouTube fan-discussion for a single game into `social_discussion`.

Composes YouTubeIngestor.fetch_for_game() with persist_comments(); the
UNIQUE(source, source_item_id) constraint + ON CONFLICT DO NOTHING makes
this safe to re-run against the same game_id.

Run from backend/:
    python scripts/ingest_youtube_game.py 0022500696
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.ingestion.youtube import YouTubeIngestor, persist_comments


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("game_id", help="e.g. 0022500696")
    args = ap.parse_args()

    session = SessionLocal()
    try:
        ingestor = YouTubeIngestor(session)
        comments = ingestor.fetch_for_game(args.game_id)
        inserted, skipped = persist_comments(session, args.game_id, comments)
        session.commit()
    finally:
        session.close()

    print(f"game_id={args.game_id}")
    print(f"  fetched:  {len(comments)}")
    print(f"  inserted: {inserted}")
    print(f"  skipped:  {skipped} (already present, dedup'd on UNIQUE(source, source_item_id))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
