from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, aliased

from app.db import engine, get_db
from app.models import (
    Game,
    GameOfficial,
    Player,
    PlayerGameStats,
    Referee,
    Team,
)
from app.schemas import (
    GameDetail,
    PlayerBoxLine,
    RefereeOut,
    RefereeProfile,
    RefGameSummary,
    TeamOut,
)

app = FastAPI(title="WhistleBlower API")


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        return {"status": "degraded", "database": "unreachable", "error": str(exc)}
    return {"status": "ok", "database": db_status}


@app.get("/teams", response_model=list[TeamOut])
def list_teams(db: Session = Depends(get_db)):
    return db.execute(select(Team).order_by(Team.name)).scalars().all()


@app.get("/games/{game_id}", response_model=GameDetail)
def get_game(game_id: str, db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"game {game_id} not found")

    home = db.get(Team, game.home_team_id)
    away = db.get(Team, game.away_team_id)

    officials = db.execute(
        select(Referee)
        .join(GameOfficial, GameOfficial.referee_id == Referee.id)
        .where(GameOfficial.game_id == game_id)
        .order_by(Referee.name)
    ).scalars().all()

    box_rows = db.execute(
        select(PlayerGameStats, Player.name)
        .join(Player, Player.id == PlayerGameStats.player_id)
        .where(PlayerGameStats.game_id == game_id)
        .order_by(PlayerGameStats.team_id, PlayerGameStats.points.desc().nulls_last())
    ).all()

    def to_line(pgs: PlayerGameStats, name: str) -> PlayerBoxLine:
        return PlayerBoxLine(
            player_id=pgs.player_id,
            name=name,
            team_id=pgs.team_id,
            minutes=pgs.minutes,
            points=pgs.points,
            rebounds_offensive=pgs.rebounds_offensive,
            rebounds_defensive=pgs.rebounds_defensive,
            rebounds_total=pgs.rebounds_total,
            assists=pgs.assists,
            steals=pgs.steals,
            blocks=pgs.blocks,
            turnovers=pgs.turnovers,
            fouls_personal=pgs.fouls_personal,
            fouls_drawn=pgs.fouls_drawn,
            field_goals_made=pgs.field_goals_made,
            field_goals_attempted=pgs.field_goals_attempted,
            three_pointers_made=pgs.three_pointers_made,
            three_pointers_attempted=pgs.three_pointers_attempted,
            free_throws_made=pgs.free_throws_made,
            free_throws_attempted=pgs.free_throws_attempted,
            plus_minus=pgs.plus_minus,
        )

    home_box = [to_line(pgs, name) for pgs, name in box_rows if pgs.team_id == game.home_team_id]
    away_box = [to_line(pgs, name) for pgs, name in box_rows if pgs.team_id == game.away_team_id]

    return GameDetail(
        id=game.id,
        date=game.date,
        season=game.season,
        home_team=TeamOut.model_validate(home),
        away_team=TeamOut.model_validate(away),
        home_score=game.home_score,
        away_score=game.away_score,
        officials=[RefereeOut.model_validate(o) for o in officials],
        home_box=home_box,
        away_box=away_box,
    )


@app.get("/referees/{referee_id}", response_model=RefereeProfile)
def get_referee(referee_id: int, db: Session = Depends(get_db)):
    ref = db.get(Referee, referee_id)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"referee {referee_id} not found")

    home_alias = aliased(Team)
    away_alias = aliased(Team)

    games = db.execute(
        select(Game.id, Game.date, home_alias.name, away_alias.name)
        .join(GameOfficial, GameOfficial.game_id == Game.id)
        .join(home_alias, home_alias.id == Game.home_team_id)
        .join(away_alias, away_alias.id == Game.away_team_id)
        .where(GameOfficial.referee_id == referee_id)
        .order_by(Game.date, Game.id)
    ).all()

    totals = db.execute(
        select(
            func.coalesce(func.sum(PlayerGameStats.fouls_personal), 0),
            func.coalesce(func.sum(PlayerGameStats.fouls_drawn), 0),
        )
        .select_from(PlayerGameStats)
        .join(GameOfficial, GameOfficial.game_id == PlayerGameStats.game_id)
        .where(GameOfficial.referee_id == referee_id)
    ).one()

    return RefereeProfile(
        id=ref.id,
        name=ref.name,
        games_officiated=len(games),
        total_fouls_personal=int(totals[0]),
        total_fouls_drawn=int(totals[1]),
        games=[
            RefGameSummary(game_id=gid, date=gdate, home_team=hname, away_team=aname)
            for gid, gdate, hname, aname in games
        ],
    )
