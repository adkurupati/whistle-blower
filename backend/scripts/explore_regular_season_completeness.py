"""
Throwaway: are BoxScoreTraditionalV3 / BoxScoreMiscV3 / BoxScoreSummaryV3
just as complete for an ordinary regular-season game as they were for the
Finals game we sampled earlier?

Approach:
  1. Pick a non-marquee, early-season regular-season game via LeagueGameFinder.
  2. Pull the same three endpoints for both that game and the Finals game.
  3. For each dataframe: diff column sets, diff shapes, and compare per-column
     null/empty counts side-by-side.

Run from backend/:
    python scripts/explore_regular_season_completeness.py
"""

from pprint import pprint

import pandas as pd
from nba_api.stats.endpoints import (
    boxscoremiscv3,
    boxscoresummaryv3,
    boxscoretraditionalv3,
    leaguegamefinder,
)

FINALS_GAME_ID = "0042500405"  # NYK @ SAS 2026-06-13, our baseline

# Teams to avoid so we don't accidentally pick a nationally televised game
MARQUEE_TRICODES = {"LAL", "GSW", "BOS", "NYK", "LAC", "MIA", "DAL", "PHI"}


def pick_regular_season_game():
    """Grab an early-season, non-marquee 2024-25 regular-season game."""
    finder = leaguegamefinder.LeagueGameFinder(
        season_nullable="2024-25",
        season_type_nullable="Regular Season",
    )
    df = finder.get_data_frames()[0]
    df = df.sort_values("GAME_DATE", ascending=True)

    for _, row in df.iterrows():
        matchup = row["MATCHUP"]
        # MATCHUP is like "MIL @ PHI" or "MIL vs. PHI" — pull out both tricodes
        tokens = [t for t in matchup.replace(".", "").split() if len(t) == 3]
        if any(t in MARQUEE_TRICODES for t in tokens):
            continue
        return row["GAME_ID"], row.to_dict()

    raise RuntimeError("no non-marquee regular-season game found")


def null_counts(df):
    """Empty-or-null count per column. Empty string counts as missing."""
    def col_missing(col):
        s = df[col]
        if s.dtype == object:
            return int(((s.isna()) | (s.astype(str).str.strip() == "")).sum())
        return int(s.isna().sum())
    return {c: col_missing(c) for c in df.columns}


def compare_endpoint(label, cls, game_id_a, game_id_b):
    print("\n" + "=" * 80)
    print(f"{label}: {game_id_a} (Finals baseline)  vs  {game_id_b} (reg-season)")
    print("=" * 80)

    a_frames = cls(game_id=game_id_a).get_data_frames()
    b_frames = cls(game_id=game_id_b).get_data_frames()

    if len(a_frames) != len(b_frames):
        print(f"  !! frame count differs: {len(a_frames)} vs {len(b_frames)}")

    for i, (a, b) in enumerate(zip(a_frames, b_frames)):
        print(f"\n--- DataFrame[{i}] ---")
        print(f"  shape:   finals={a.shape}   reg={b.shape}")

        cols_a, cols_b = set(a.columns), set(b.columns)
        only_a, only_b = cols_a - cols_b, cols_b - cols_a
        if only_a or only_b:
            print(f"  !! column set differs. finals-only: {only_a}   reg-only: {only_b}")
        else:
            print("  columns: identical")

        # Per-column missingness delta
        shared = [c for c in a.columns if c in b.columns]
        miss_a, miss_b = null_counts(a[shared]), null_counts(b[shared])
        deltas = []
        for c in shared:
            # normalize by row count so a bigger df doesn't spuriously "look worse"
            pct_a = miss_a[c] / len(a) if len(a) else 0
            pct_b = miss_b[c] / len(b) if len(b) else 0
            if abs(pct_a - pct_b) > 0.01 or (miss_a[c] == 0 and miss_b[c] > 0):
                deltas.append((c, miss_a[c], len(a), miss_b[c], len(b)))
        if deltas:
            print("  !! per-column missingness differences (col, finals_missing/rows, reg_missing/rows):")
            for c, ma, la, mb, lb in deltas:
                print(f"      {c:35s}  finals={ma}/{la}   reg={mb}/{lb}")
        else:
            print("  missingness: no notable per-column difference")


def dump_officials_side_by_side(game_a, game_b):
    print("\n" + "=" * 80)
    print("Officials block — side by side (raw)")
    print("=" * 80)
    for label, gid in (("FINALS", game_a), ("REG-SEASON", game_b)):
        raw = boxscoresummaryv3.BoxScoreSummaryV3(game_id=gid).get_dict()
        officials = raw["boxScoreSummary"].get("officials", [])
        print(f"\n{label} game_id={gid}  → {len(officials)} officials")
        for o in officials:
            print(f"  personId={o.get('personId')}  name={o.get('name')!r}  "
                  f"jerseyNum={o.get('jerseyNum')!r}  assignment={o.get('assignment')!r}")


def main():
    game_id, row = pick_regular_season_game()
    print(f"Chose regular-season game: {row['GAME_DATE']}  {row['MATCHUP']}  id={game_id}")

    compare_endpoint("BoxScoreTraditionalV3",
                     boxscoretraditionalv3.BoxScoreTraditionalV3,
                     FINALS_GAME_ID, game_id)
    compare_endpoint("BoxScoreMiscV3",
                     boxscoremiscv3.BoxScoreMiscV3,
                     FINALS_GAME_ID, game_id)
    compare_endpoint("BoxScoreSummaryV3",
                     boxscoresummaryv3.BoxScoreSummaryV3,
                     FINALS_GAME_ID, game_id)
    dump_officials_side_by_side(FINALS_GAME_ID, game_id)


if __name__ == "__main__":
    main()
