"""
Throwaway: does nba_api expose 'fouls drawn' anywhere separate from foulsPersonal?

Hits every box-score variant the package exposes for a single known game
and prints every field name that contains 'foul' (case-insensitive) plus
the full column list per dataframe so we can eyeball the rest.

Run from backend/:
    python scripts/explore_fouls_drawn.py
"""

from nba_api.stats.endpoints import (
    boxscoreadvancedv3,
    boxscorefourfactorsv3,
    boxscorehustlev2,
    boxscorematchupsv3,
    boxscoremiscv3,
    boxscoreplayertrackv3,
    boxscorescoringv3,
    boxscoreusagev3,
    boxscoredefensivev2,
    hustlestatsboxscore,
)

GAME_ID = "0042500405"  # NYK @ SAS, 2026-06-13 (from prior exploration)

ENDPOINTS = [
    ("BoxScoreAdvancedV3",     boxscoreadvancedv3.BoxScoreAdvancedV3),
    ("BoxScoreMiscV3",         boxscoremiscv3.BoxScoreMiscV3),
    ("BoxScoreScoringV3",      boxscorescoringv3.BoxScoreScoringV3),
    ("BoxScoreUsageV3",        boxscoreusagev3.BoxScoreUsageV3),
    ("BoxScoreFourFactorsV3",  boxscorefourfactorsv3.BoxScoreFourFactorsV3),
    ("BoxScorePlayerTrackV3",  boxscoreplayertrackv3.BoxScorePlayerTrackV3),
    ("BoxScoreMatchupsV3",     boxscorematchupsv3.BoxScoreMatchupsV3),
    ("BoxScoreHustleV2",       boxscorehustlev2.BoxScoreHustleV2),
    ("HustleStatsBoxScore",    hustlestatsboxscore.HustleStatsBoxScore),
    ("BoxScoreDefensiveV2",    boxscoredefensivev2.BoxScoreDefensiveV2),
]


def inspect(label, cls):
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)
    try:
        ep = cls(game_id=GAME_ID)
        frames = ep.get_data_frames()
    except Exception as exc:
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        return

    hit_any_foul = False
    for i, df in enumerate(frames):
        cols = list(df.columns)
        foul_cols = [c for c in cols if "foul" in c.lower()]
        print(f"\n  DataFrame[{i}] shape={df.shape}")
        print(f"  all columns: {cols}")
        if foul_cols:
            hit_any_foul = True
            print(f"  >>> FOUL-RELATED FIELDS: {foul_cols}")
    if not hit_any_foul:
        print("\n  (no 'foul'-named columns in any dataframe)")


def main():
    for label, cls in ENDPOINTS:
        inspect(label, cls)


if __name__ == "__main__":
    main()
