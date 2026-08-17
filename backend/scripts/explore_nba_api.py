"""
Throwaway exploration script. NOT part of the app.

Goal: pull one real recent NBA game via nba_api and dump the raw shape of:
  1. team info (from the game-finder row)
  2. player box score (points, fouls, etc.)
  3. officials / referee assignments (from BoxScoreSummaryV3)

We print the raw response structure so we can eyeball fields before
committing to a Postgres schema.

Run from backend/:
    python scripts/explore_nba_api.py
"""

import json
from pprint import pprint

from nba_api.stats.endpoints import (
    boxscoresummaryv3,
    boxscoretraditionalv3,
    leaguegamefinder,
)


def find_recent_game():
    """Return (game_id, row_dict, season) for the most recent game we can find.

    Tries the current season first, falls back to the previous one because
    nba_api sometimes returns empty for a season that hasn't started tipping
    off yet (we're running this in August)."""
    for season in ("2025-26", "2024-25"):
        finder = leaguegamefinder.LeagueGameFinder(season_nullable=season)
        df = finder.get_data_frames()[0]
        if not df.empty:
            # rows are per-team-per-game, so a single game_id appears twice.
            # Sort descending by date to be safe about ordering.
            df = df.sort_values("GAME_DATE", ascending=False)
            row = df.iloc[0]
            return row["GAME_ID"], row.to_dict(), season
    raise RuntimeError("LeagueGameFinder returned no games for either season")


def banner(label):
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)


def dump_dataframes(endpoint):
    """Print the columns of every DataFrame the endpoint exposes,
    plus one sample row per frame so we can see field values."""
    frames = endpoint.get_data_frames()
    for i, df in enumerate(frames):
        print(f"\n--- DataFrame[{i}] shape={df.shape} ---")
        print("columns:", list(df.columns))
        if not df.empty:
            print("sample row:")
            pprint(df.iloc[0].to_dict(), sort_dicts=False)


def dump_raw_dict(endpoint, max_chars=4000):
    """Print the raw JSON dict returned by the endpoint (truncated).

    v3 endpoints return a nested dict rather than the v2 resultSets list —
    printing the raw shape shows exactly what the schema needs to model."""
    raw = endpoint.get_dict()
    text = json.dumps(raw, indent=2, default=str)
    if len(text) > max_chars:
        print(text[:max_chars])
        print(f"... [truncated, total {len(text)} chars]")
    else:
        print(text)


def main():
    banner("STEP 1: find a recent game")
    game_id, row, season = find_recent_game()
    print(f"season={season}  game_id={game_id}")
    print(f"date={row.get('GAME_DATE')}  matchup={row.get('MATCHUP')}")
    print("\nAll fields on the game-finder row (team-perspective row):")
    pprint(row, sort_dicts=False)

    banner("STEP 2: BoxScoreTraditionalV3 — player stats (points, fouls, etc.)")
    trad = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
    print("\n>>> DataFrame view (columns + first row per frame):")
    dump_dataframes(trad)
    print("\n>>> Raw dict view (truncated):")
    dump_raw_dict(trad, max_chars=3000)

    banner("STEP 3: BoxScoreSummaryV3 — officials / referee assignments")
    summary = boxscoresummaryv3.BoxScoreSummaryV3(game_id=game_id)
    print("\n>>> DataFrame view (columns + first row per frame):")
    dump_dataframes(summary)
    print("\n>>> Raw dict view (full — this is the schema-driving one):")
    dump_raw_dict(summary, max_chars=8000)


if __name__ == "__main__":
    main()
