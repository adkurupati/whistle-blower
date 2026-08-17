"""
End-to-end proof-of-life ingestion for a single date's games.

Pulls every game on TARGET_DATE and writes teams, players, referees, games,
game_officials, and player_game_stats into Postgres. Merges BoxScoreTraditionalV3
(foulsPersonal + standard stats) with BoxScoreMiscV3 (foulsDrawn) on personId.

Upserts via Postgres ON CONFLICT DO NOTHING so re-running is safe.

Run from backend/:
    python scripts/ingest_one_day.py
"""

import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from nba_api.stats.endpoints import (
    boxscoremiscv3,
    boxscoresummaryv3,
    boxscoretraditionalv3,
    leaguegamefinder,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import (
    Game,
    GameOfficial,
    Player,
    PlayerGameStats,
    Referee,
    Team,
)

TARGET_DATE = "2024-10-23"
SEASON = "2024-25"
API_SLEEP_SEC = 0.6  # space out stats.nba.com calls; without this we hit throttling


# ---------- helpers ----------

def nan_to_none(v):
    """pandas gives NaN for missing numerics; Postgres wants NULL."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def to_int_or_none(v):
    v = nan_to_none(v)
    return int(v) if v is not None else None


def derive_season(d: date) -> str:
    start = d.year if d.month >= 10 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def upsert(session, model, rows):
    """Postgres INSERT ... ON CONFLICT DO NOTHING against any constraint."""
    if not rows:
        return
    stmt = insert(model).values(rows).on_conflict_do_nothing()
    session.execute(stmt)


# ---------- fetch ----------

def find_games_on_date(target: str) -> list[str]:
    finder = leaguegamefinder.LeagueGameFinder(
        season_nullable=SEASON,
        season_type_nullable="Regular Season",
    )
    df = finder.get_data_frames()[0]
    df = df[df["GAME_DATE"] == target]
    return sorted(df["GAME_ID"].unique().tolist())


# ---------- ingest one game ----------

def ingest_game(session, game_id: str):
    summary = boxscoresummaryv3.BoxScoreSummaryV3(game_id=game_id).get_dict()
    s = summary["boxScoreSummary"]
    time.sleep(API_SLEEP_SEC)

    trad = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
    players_df = trad.get_data_frames()[0]
    team_totals_df = trad.get_data_frames()[2]
    time.sleep(API_SLEEP_SEC)

    misc = boxscoremiscv3.BoxScoreMiscV3(game_id=game_id).get_data_frames()[0]
    fouls_drawn_by_person = {
        int(pid): to_int_or_none(fd)
        for pid, fd in zip(misc["personId"], misc["foulsDrawn"])
    }

    home_team_id = int(s["homeTeamId"])
    away_team_id = int(s["awayTeamId"])
    officials = s.get("officials", [])
    game_date = datetime.fromisoformat(s["gameEt"].replace("Z", "+00:00")).date()

    # Teams (from the team totals frame — has city + name we can concatenate)
    upsert(session, Team, [
        {"id": int(r["teamId"]),
         "name": f"{r['teamCity']} {r['teamName']}".strip()}
        for _, r in team_totals_df.iterrows()
    ])

    # Players
    upsert(session, Player, [
        {"id": int(r["personId"]),
         "name": f"{r['firstName']} {r['familyName']}".strip()}
        for _, r in players_df.iterrows()
    ])

    # Referees
    upsert(session, Referee, [
        {"id": int(o["personId"]), "name": o["name"]}
        for o in officials
    ])

    # Game
    scores_by_team = {
        int(tid): to_int_or_none(pts)
        for tid, pts in zip(team_totals_df["teamId"], team_totals_df["points"])
    }
    upsert(session, Game, [{
        "id": game_id,
        "date": game_date,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "season": derive_season(game_date),
        "home_score": scores_by_team.get(home_team_id),
        "away_score": scores_by_team.get(away_team_id),
    }])

    # Game officials
    upsert(session, GameOfficial, [
        {"game_id": game_id, "referee_id": int(o["personId"])}
        for o in officials
    ])

    # Player game stats (merge traditional + misc on personId)
    pgs_rows = []
    for _, r in players_df.iterrows():
        pid = int(r["personId"])
        pgs_rows.append({
            "game_id": game_id,
            "player_id": pid,
            "team_id": int(r["teamId"]),
            "minutes": nan_to_none(r.get("minutes")) or None,
            "points": to_int_or_none(r.get("points")),
            "rebounds_offensive": to_int_or_none(r.get("reboundsOffensive")),
            "rebounds_defensive": to_int_or_none(r.get("reboundsDefensive")),
            "rebounds_total": to_int_or_none(r.get("reboundsTotal")),
            "assists": to_int_or_none(r.get("assists")),
            "steals": to_int_or_none(r.get("steals")),
            "blocks": to_int_or_none(r.get("blocks")),
            "turnovers": to_int_or_none(r.get("turnovers")),
            "fouls_personal": to_int_or_none(r.get("foulsPersonal")),
            "fouls_drawn": fouls_drawn_by_person.get(pid),
            "field_goals_made": to_int_or_none(r.get("fieldGoalsMade")),
            "field_goals_attempted": to_int_or_none(r.get("fieldGoalsAttempted")),
            "three_pointers_made": to_int_or_none(r.get("threePointersMade")),
            "three_pointers_attempted": to_int_or_none(r.get("threePointersAttempted")),
            "free_throws_made": to_int_or_none(r.get("freeThrowsMade")),
            "free_throws_attempted": to_int_or_none(r.get("freeThrowsAttempted")),
            "plus_minus": to_int_or_none(r.get("plusMinusPoints")),
        })
    upsert(session, PlayerGameStats, pgs_rows)

    print(f"  {game_id}: {len(players_df)} players, "
          f"{len(officials)} officials, "
          f"home={home_team_id}({scores_by_team.get(home_team_id)}) "
          f"away={away_team_id}({scores_by_team.get(away_team_id)})")


# ---------- verification ----------

def print_verification(session):
    print("\n" + "=" * 80)
    print("Row counts (all-time in this DB, not just this run):")
    print("=" * 80)
    for model in [Team, Player, Referee, Game, GameOfficial, PlayerGameStats]:
        n = session.execute(select(func.count()).select_from(model)).scalar()
        print(f"  {model.__tablename__:22s}  {n}")

    print("\nAll games ingested:")
    for g in session.execute(select(Game).order_by(Game.id)).scalars():
        print(f"  {g.id}  {g.date}  {g.season}  "
              f"home_team={g.home_team_id} ({g.home_score})  "
              f"away_team={g.away_team_id} ({g.away_score})")

    print("\nGame officials by game:")
    stmt = (select(GameOfficial.game_id, Referee.name)
            .join(Referee, Referee.id == GameOfficial.referee_id)
            .order_by(GameOfficial.game_id, Referee.name))
    for gid, rname in session.execute(stmt).all():
        print(f"  {gid}  {rname}")

    print("\nTop-5 scorers across the day (proves the traditional-box side landed):")
    stmt = (select(Player.name, PlayerGameStats.points,
                   PlayerGameStats.fouls_personal, PlayerGameStats.minutes,
                   PlayerGameStats.game_id)
            .join(Player, Player.id == PlayerGameStats.player_id)
            .order_by(PlayerGameStats.points.desc().nulls_last())
            .limit(5))
    for name, pts, pf, mins, gid in session.execute(stmt).all():
        print(f"  {name:28s}  pts={pts:>3}  fouls_personal={pf}  min={mins}  ({gid})")

    print("\nTop-5 by fouls_drawn (proves the misc-box side landed AND the join worked):")
    stmt = (select(Player.name, PlayerGameStats.fouls_drawn,
                   PlayerGameStats.fouls_personal, PlayerGameStats.points,
                   PlayerGameStats.game_id)
            .join(Player, Player.id == PlayerGameStats.player_id)
            .order_by(PlayerGameStats.fouls_drawn.desc().nulls_last())
            .limit(5))
    for name, fd, pf, pts, gid in session.execute(stmt).all():
        print(f"  {name:28s}  fouls_drawn={fd}  fouls_personal={pf}  pts={pts}  ({gid})")


def main():
    print(f"Finding games for {TARGET_DATE} ...")
    game_ids = find_games_on_date(TARGET_DATE)
    print(f"Found {len(game_ids)}: {game_ids}")

    with SessionLocal() as session:
        for gid in game_ids:
            ingest_game(session, gid)
            session.commit()  # per-game commit so partial progress survives
        print_verification(session)


if __name__ == "__main__":
    main()
