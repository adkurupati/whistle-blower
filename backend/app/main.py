from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db import engine, get_db
from app.models import (
    FollowedTeam,
    Game,
    GameOfficial,
    L2MReport,
    NotificationPref,
    Player,
    PlayerGameStats,
    Referee,
    RefVote,
    Team,
    User,
)
from app.schemas import (
    GameDetail,
    LoginIn,
    NotificationPrefsIn,
    NotificationPrefsOut,
    PlayerBoxLine,
    RankingsSummary,
    RefereeOut,
    RefereeProfile,
    RefereeRankingRow,
    RefereeRankingsResponse,
    RefGameSummary,
    SignupIn,
    TeamOut,
    TokenOut,
    UserOut,
    VoteIn,
    VoteOut,
)
from app.scoring import compute_rankings, league_average_correct_rate

app = FastAPI(title="WhistleBlower API")

# Vite dev server runs on 5173. Add more origins here when deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        return {"status": "degraded", "database": "unreachable", "error": str(exc)}
    return {"status": "ok", "database": db_status}


@app.post("/auth/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    existing = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    token, expires_in = create_access_token(user.id)
    return TokenOut(access_token=token, expires_in_seconds=expires_in)


@app.post("/auth/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    token, expires_in = create_access_token(user.id)
    return TokenOut(access_token=token, expires_in_seconds=expires_in)


@app.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@app.post("/me/followed-teams/{team_id}", response_model=TeamOut)
def follow_team(
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Idempotent: following a team you already follow is a no-op success."""
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"team {team_id} not found")
    stmt = (
        pg_insert(FollowedTeam)
        .values(user_id=user.id, team_id=team_id)
        .on_conflict_do_nothing()
    )
    db.execute(stmt)
    db.commit()
    return team


@app.delete("/me/followed-teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def unfollow_team(
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Idempotent: unfollowing a team you don't follow is a no-op success."""
    db.execute(
        delete(FollowedTeam).where(
            FollowedTeam.user_id == user.id,
            FollowedTeam.team_id == team_id,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/me/followed-teams", response_model=list[TeamOut])
def list_followed_teams(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return (
        db.execute(
            select(Team)
            .join(FollowedTeam, FollowedTeam.team_id == Team.id)
            .where(FollowedTeam.user_id == user.id)
            .order_by(Team.name)
        )
        .scalars()
        .all()
    )


@app.get("/me/notification-prefs", response_model=NotificationPrefsOut)
def get_notification_prefs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    prefs = db.get(NotificationPref, user.id)
    if prefs is None:
        # Default: digest is on when no explicit prefs row exists (matches
        # the column's server_default). updated_at stays null so the caller
        # can tell they're seeing defaults, not a persisted choice.
        return NotificationPrefsOut(digest_enabled=True, updated_at=None)
    return prefs


@app.patch("/me/notification-prefs", response_model=NotificationPrefsOut)
def update_notification_prefs(
    payload: NotificationPrefsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        pg_insert(NotificationPref)
        .values(
            user_id=user.id,
            digest_enabled=payload.digest_enabled,
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=[NotificationPref.user_id],
            set_={
                "digest_enabled": payload.digest_enabled,
                "updated_at": func.now(),
            },
        )
        .returning(NotificationPref)
    )
    prefs = db.execute(stmt).scalar_one()
    db.commit()
    db.refresh(prefs)
    return prefs


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


@app.get("/referees/rankings", response_model=RefereeRankingsResponse)
def referee_rankings(db: Session = Depends(get_db)):
    """Verified Ranking — Official Score per ref, sorted best to worst,
    plus league-wide summary stats used in the shrinkage prior."""
    scored = compute_rankings(db)
    games_reviewed = db.execute(select(func.count()).select_from(L2MReport)).scalar_one()
    return RefereeRankingsResponse(
        summary=RankingsSummary(
            games_reviewed=games_reviewed,
            league_avg_correct_rate=league_average_correct_rate(db),
        ),
        rankings=[
            RefereeRankingRow(
                rank=r.rank,
                referee_id=r.referee_id,
                name=r.name,
                total_calls_graded=r.total_calls_graded,
                raw_correct_rate=r.raw_correct_rate,
                shrunk_rate=r.shrunk_rate,
            )
            for r in scored
        ],
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

    # Official Score comes from the ranking computation — pull just this ref
    # from the full list (78 rows, single aggregation, cheap enough).
    my_score = next(
        (r.shrunk_rate for r in compute_rankings(db) if r.referee_id == referee_id),
        None,
    )

    audience_avg = db.execute(
        select(func.avg(RefVote.rating_value)).where(RefVote.referee_id == referee_id)
    ).scalar()
    audience_score = float(audience_avg) if audience_avg is not None else None

    return RefereeProfile(
        id=ref.id,
        name=ref.name,
        games_officiated=len(games),
        official_score=my_score,
        audience_score=audience_score,
        total_fouls_personal=int(totals[0]),
        total_fouls_drawn=int(totals[1]),
        games=[
            RefGameSummary(game_id=gid, date=gdate, home_team=hname, away_team=aname)
            for gid, gdate, hname, aname in games
        ],
    )


def _ensure_game_and_ref(db: Session, game_id: str, referee_id: int) -> None:
    if db.get(Game, game_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"game {game_id} not found")
    if db.get(Referee, referee_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"referee {referee_id} not found")


@app.post(
    "/games/{game_id}/referees/{referee_id}/vote",
    response_model=VoteOut,
)
def cast_vote(
    game_id: str,
    referee_id: int,
    payload: VoteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cast (or update) this user's 1–5 rating for a referee in a specific game.
    Idempotent per (user, referee, game) — re-voting overwrites the rating."""
    _ensure_game_and_ref(db, game_id, referee_id)

    stmt = (
        pg_insert(RefVote)
        .values(
            user_id=user.id,
            referee_id=referee_id,
            game_id=game_id,
            rating_value=payload.rating,
        )
        .on_conflict_do_update(
            constraint="uq_ref_votes_user_ref_game",
            set_={"rating_value": payload.rating},
        )
        .returning(RefVote)
    )
    vote = db.execute(stmt).scalar_one()
    db.commit()
    db.refresh(vote)
    return vote


@app.get(
    "/games/{game_id}/referees/{referee_id}/vote",
    response_model=VoteOut,
)
def get_my_vote(
    game_id: str,
    referee_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the calling user's own vote for this ref+game, or 404 if none."""
    _ensure_game_and_ref(db, game_id, referee_id)

    vote = db.execute(
        select(RefVote).where(
            RefVote.user_id == user.id,
            RefVote.referee_id == referee_id,
            RefVote.game_id == game_id,
        )
    ).scalar_one_or_none()

    if vote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no vote yet")
    return vote
