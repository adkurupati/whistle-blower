import datetime

from pydantic import BaseModel, ConfigDict, Field


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class RefereeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class PlayerBoxLine(BaseModel):
    player_id: int
    name: str
    team_id: int
    minutes: str | None = None
    points: int | None = None
    rebounds_offensive: int | None = None
    rebounds_defensive: int | None = None
    rebounds_total: int | None = None
    assists: int | None = None
    steals: int | None = None
    blocks: int | None = None
    turnovers: int | None = None
    fouls_personal: int | None = None
    fouls_drawn: int | None = None
    field_goals_made: int | None = None
    field_goals_attempted: int | None = None
    three_pointers_made: int | None = None
    three_pointers_attempted: int | None = None
    free_throws_made: int | None = None
    free_throws_attempted: int | None = None
    plus_minus: int | None = None


class GameDetail(BaseModel):
    id: str
    date: datetime.date
    season: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None
    away_score: int | None
    officials: list[RefereeOut]
    home_box: list[PlayerBoxLine]
    away_box: list[PlayerBoxLine]


class RefGameSummary(BaseModel):
    game_id: str
    date: datetime.date
    home_team: str
    away_team: str


class RefereeProfile(BaseModel):
    id: int
    name: str
    games_officiated: int
    # These sum every player's fouls_personal / fouls_drawn across every game
    # this ref worked. Since 3 refs work each game, the same fouls are attributed
    # to all three — this is a raw game-level signal, not a per-ref call count.
    total_fouls_personal: int = Field(
        description="Sum of player fouls committed across all games this ref officiated"
    )
    total_fouls_drawn: int = Field(
        description="Sum of player fouls drawn across all games this ref officiated"
    )
    games: list[RefGameSummary]
