"""
Ingest every game in a given month. Reuses ingest_game() from ingest_one_day.

Same pacing (0.6s between endpoint calls), same idempotent upserts,
same per-game commits — plus progress logging and a per-game try/except
so one throttled call doesn't kill the whole batch.

Run from backend/:
    python scripts/ingest_month.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nba_api.stats.endpoints import leaguegamefinder
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    Game, GameOfficial, Player, PlayerGameStats, Referee, Team,
)
from ingest_one_day import SEASON, ingest_game

TARGET_YEAR = 2024
TARGET_MONTH = 11  # November


def find_games_in_month(year: int, month: int) -> list[tuple[str, str]]:
    """Return sorted list of (date, game_id) for regular-season games in the month."""
    finder = leaguegamefinder.LeagueGameFinder(
        season_nullable=SEASON,
        season_type_nullable="Regular Season",
    )
    df = finder.get_data_frames()[0]
    prefix = f"{year}-{month:02d}-"
    df = df[df["GAME_DATE"].str.startswith(prefix)]
    return sorted(set(zip(df["GAME_DATE"], df["GAME_ID"])))


def print_progress(idx: int, total: int, gid: str, gdate: str, t_start: float,
                   failures: int) -> None:
    elapsed = time.time() - t_start
    per_game = elapsed / idx if idx else 0
    eta = per_game * (total - idx)
    print(
        f"[{idx:>3}/{total}] {gdate} {gid}  "
        f"elapsed={elapsed:>5.0f}s  eta={eta:>5.0f}s  fails={failures}",
        flush=True,
    )


def print_final_counts(session):
    print("\n" + "=" * 80)
    print("Final row counts (cumulative across everything ever ingested)")
    print("=" * 80)
    for model in [Team, Player, Referee, Game, GameOfficial, PlayerGameStats]:
        n = session.execute(select(func.count()).select_from(model)).scalar()
        print(f"  {model.__tablename__:22s}  {n}")


def main():
    print(f"Fetching game list for {TARGET_YEAR}-{TARGET_MONTH:02d} ...", flush=True)
    pairs = find_games_in_month(TARGET_YEAR, TARGET_MONTH)
    total = len(pairs)
    print(f"Found {total} games", flush=True)

    t_start = time.time()
    failures = 0

    with SessionLocal() as session:
        for i, (gdate, gid) in enumerate(pairs, 1):
            try:
                ingest_game(session, gid)
                session.commit()
            except Exception as exc:
                session.rollback()
                failures += 1
                print(f"    ! FAIL {gid}: {type(exc).__name__}: {exc}", flush=True)
            print_progress(i, total, gid, gdate, t_start, failures)

        print_final_counts(session)

    print(f"\nDone. total={total}  failures={failures}  "
          f"elapsed={time.time() - t_start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
