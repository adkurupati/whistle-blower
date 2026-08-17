import datetime

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Referee(Base):
    __tablename__ = "referees"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Game(Base):
    __tablename__ = "games"

    # VARCHAR to preserve zero-padding and the season-type prefix (e.g. "0042500405")
    id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    home_team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teams.id"), nullable=False
    )
    away_team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teams.id"), nullable=False
    )
    season: Mapped[str] = mapped_column(String, nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)


class GameOfficial(Base):
    __tablename__ = "game_officials"

    # No role column: BoxScoreSummaryV3.officials[].assignment is empty in
    # every response we've seen, so crew-chief/referee/umpire isn't reliably
    # derivable from this data source. Crew size varies (3 in regular season,
    # 4 in Finals when an alternate is listed) — no CHECK constraint on count.
    game_id: Mapped[str] = mapped_column(
        String, ForeignKey("games.id"), primary_key=True
    )
    referee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("referees.id"), primary_key=True
    )


class PlayerGameStats(Base):
    __tablename__ = "player_game_stats"

    game_id: Mapped[str] = mapped_column(
        String, ForeignKey("games.id"), primary_key=True
    )
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.id"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teams.id"), nullable=False
    )

    minutes: Mapped[str | None] = mapped_column(String)
    points: Mapped[int | None] = mapped_column(Integer)
    rebounds_offensive: Mapped[int | None] = mapped_column(Integer)
    rebounds_defensive: Mapped[int | None] = mapped_column(Integer)
    rebounds_total: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    steals: Mapped[int | None] = mapped_column(Integer)
    blocks: Mapped[int | None] = mapped_column(Integer)
    turnovers: Mapped[int | None] = mapped_column(Integer)

    # Committed (BoxScoreTraditionalV3.foulsPersonal) and drawn
    # (BoxScoreMiscV3.foulsDrawn) — two separate endpoints, joined on
    # (gameId, personId) at ingest time.
    fouls_personal: Mapped[int | None] = mapped_column(Integer)
    fouls_drawn: Mapped[int | None] = mapped_column(Integer)

    field_goals_made: Mapped[int | None] = mapped_column(Integer)
    field_goals_attempted: Mapped[int | None] = mapped_column(Integer)
    three_pointers_made: Mapped[int | None] = mapped_column(Integer)
    three_pointers_attempted: Mapped[int | None] = mapped_column(Integer)
    free_throws_made: Mapped[int | None] = mapped_column(Integer)
    free_throws_attempted: Mapped[int | None] = mapped_column(Integer)
    plus_minus: Mapped[int | None] = mapped_column(Integer)
