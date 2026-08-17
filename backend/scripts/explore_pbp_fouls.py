"""
Throwaway: does play-by-play give us enough detail to attribute
a foul to a specific player + quarter?

Pulls PlayByPlayV3 for one game, prints the column list, and dumps the
raw rows for the first few foul events.

(PlayByPlayV2 is deprecated — the NBA API returns empty JSON for it as of
2025+; use V3.)

Run from backend/:
    python scripts/explore_pbp_fouls.py
"""

from pprint import pprint

from nba_api.stats.endpoints import playbyplayv3

GAME_ID = "0042500405"


def banner(label):
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)


def dump_v3():
    banner("PlayByPlayV3")
    ep = playbyplayv3.PlayByPlayV3(game_id=GAME_ID)
    frames = ep.get_data_frames()

    for i, df in enumerate(frames):
        print(f"\n--- DataFrame[{i}] shape={df.shape} ---")
        print("columns:", list(df.columns))

    # Foul events: V3 uses a string 'actionType' column.
    df0 = frames[0]
    if "actionType" in df0.columns:
        fouls = df0[df0["actionType"].str.contains("foul", case=False, na=False)]
    else:
        # Fallback — inspect whatever event-type column exists
        candidates = [c for c in df0.columns if "type" in c.lower() or "event" in c.lower()]
        print(f"\n(no 'actionType' column; candidates: {candidates})")
        return

    print(f"\nFound {len(fouls)} rows matching actionType~'foul' out of {len(df0)} total events.")
    print("\nUnique actionType values seen among those rows:")
    pprint(fouls["actionType"].value_counts().to_dict())
    if "subType" in fouls.columns:
        print("\nUnique subType values seen among those rows:")
        pprint(fouls["subType"].value_counts().to_dict())

    print("\nFirst 3 foul events (all fields):")
    for _, row in fouls.head(3).iterrows():
        print("\n---")
        pprint(row.to_dict(), sort_dicts=False)


def main():
    dump_v3()


if __name__ == "__main__":
    main()
