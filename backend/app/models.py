import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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


class L2MReport(Base):
    __tablename__ = "l2m_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String, ForeignKey("games.id"), nullable=False, unique=True
    )
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    # The L2M endpoint doesn't expose a real publish timestamp; we set this
    # to the fetch time at ingest and treat it as "known-published by this time."
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class L2MCall(Base):
    __tablename__ = "l2m_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    l2m_report_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("l2m_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    period: Mapped[str] = mapped_column(String, nullable=False)      # "Q4", "OT1"
    pc_time: Mapped[str] = mapped_column(String, nullable=False)     # "02:00.0"
    call_type: Mapped[str] = mapped_column(String, nullable=False)   # "Foul: Personal"
    call_rating: Mapped[str | None] = mapped_column(String)          # CC / CNC / IC / INC / null

    # Raw strings preserved even when name→id resolution fails (DP can be a team name).
    committing_player_name: Mapped[str] = mapped_column(String, nullable=False)
    disadvantaged_player_name: Mapped[str] = mapped_column(String, nullable=False)
    committing_player_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.id")
    )
    disadvantaged_player_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.id")
    )

    pos_team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teams.id"), nullable=False
    )
    team_id_in_favor: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("teams.id")
    )

    nba_comment: Mapped[str] = mapped_column(Text, nullable=False)
    video_link_id: Mapped[str | None] = mapped_column(String)


class RefVote(Base):
    __tablename__ = "ref_votes"
    __table_args__ = (
        UniqueConstraint("user_id", "referee_id", "game_id", name="uq_ref_votes_user_ref_game"),
        CheckConstraint("rating_value BETWEEN 1 AND 5", name="ck_ref_votes_rating_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    referee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("referees.id"), nullable=False
    )
    game_id: Mapped[str] = mapped_column(
        String, ForeignKey("games.id"), nullable=False
    )
    rating_value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FollowedTeam(Base):
    __tablename__ = "followed_teams"

    # Composite PK — user_id + team_id — is itself the uniqueness guarantee
    # the spec asked for, so no separate id column or extra unique index.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teams.id"), primary_key=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationPref(Base):
    __tablename__ = "notification_prefs"

    # One row per user, so user_id IS the primary key.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), primary_key=True
    )
    digest_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Set manually on PATCH — Postgres has no cross-tool server_onupdate,
    # and pg_insert's ON CONFLICT DO UPDATE won't fire SQLAlchemy's onupdate.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
