"""
Compute per-user digests of L2M-graded missed calls (IC / INC) across a set
of games, for every user with digest_enabled who follows a team involved
in one of those games.

NOT connected to email. `deliver_digest(user, digest)` just prints for
now — replace it with a real SES call in Phase 10, nothing else changes.

Data caveat carried through into the digest text: `l2m_calls.team_id_in_favor`
is always null in practice, so we cannot tell which team benefited from
a bad call. "Affected" therefore means "your team was on the floor when
a missed call happened," not "your team was specifically wronged."

Run from backend/:
    python scripts/compute_digest.py                        # every ingested game
    python scripts/compute_digest.py 0022400002 0022400132  # specific games
"""

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    FollowedTeam,
    Game,
    L2MCall,
    L2MReport,
    NotificationPref,
    Team,
    User,
)


BAD_RATINGS = ("IC", "INC")


@dataclass
class AffectedGame:
    game_id: str
    date: str
    matchup: str
    bad_calls: int


@dataclass
class AffectedTeam:
    team_id: int
    team_name: str
    missed_calls: int
    games: list[AffectedGame]


@dataclass
class Digest:
    user_email: str
    affected_teams: list[AffectedTeam] = field(default_factory=list)


def compute_digests(session: Session, game_ids: list[str] | None) -> list[tuple[User, Digest]]:
    """Return (user, digest) pairs for every eligible user with content to send."""

    # 1. For every game (optionally filtered) find count of IC/INC calls
    q = (
        select(
            Game.id,
            Game.date,
            Game.home_team_id,
            Game.away_team_id,
            func.count(L2MCall.id),
        )
        .select_from(Game)
        .join(L2MReport, L2MReport.game_id == Game.id)
        .join(L2MCall, L2MCall.l2m_report_id == L2MReport.id)
        .where(L2MCall.call_rating.in_(BAD_RATINGS))
        .group_by(Game.id, Game.date, Game.home_team_id, Game.away_team_id)
    )
    if game_ids:
        q = q.where(Game.id.in_(game_ids))
    game_rows = session.execute(q).all()
    if not game_rows:
        return []

    # 2. Resolve team names for every home/away id we hit
    team_ids_needed: set[int] = set()
    for _, _, home_id, away_id, _ in game_rows:
        team_ids_needed.add(home_id)
        team_ids_needed.add(away_id)
    teams_by_id: dict[int, Team] = {
        t.id: t
        for t in session.execute(
            select(Team).where(Team.id.in_(team_ids_needed))
        ).scalars()
    }

    # 3. Build team_id -> [AffectedGame,...] for every affected team
    team_to_games: dict[int, list[AffectedGame]] = defaultdict(list)
    affected_team_ids: set[int] = set()
    for game_id, gdate, home_id, away_id, bad_count in game_rows:
        affected_team_ids.add(home_id)
        affected_team_ids.add(away_id)
        matchup = (
            f"{teams_by_id[away_id].name} @ {teams_by_id[home_id].name}"
        )
        ag = AffectedGame(
            game_id=game_id,
            date=str(gdate),
            matchup=matchup,
            bad_calls=int(bad_count),
        )
        team_to_games[home_id].append(ag)
        team_to_games[away_id].append(ag)

    # 4. Users with digest_enabled=true (or no prefs row = default true)
    #    who follow at least one affected team
    user_rows = session.execute(
        select(User, FollowedTeam.team_id)
        .join(FollowedTeam, FollowedTeam.user_id == User.id)
        .outerjoin(NotificationPref, NotificationPref.user_id == User.id)
        .where(FollowedTeam.team_id.in_(affected_team_ids))
        .where(func.coalesce(NotificationPref.digest_enabled, True).is_(True))
        .order_by(User.id, FollowedTeam.team_id)
    ).all()

    # 5. Group per user
    per_user: dict[int, tuple[User, list[int]]] = {}
    for user, team_id in user_rows:
        if user.id not in per_user:
            per_user[user.id] = (user, [])
        per_user[user.id][1].append(team_id)

    # 6. Assemble digests
    digests: list[tuple[User, Digest]] = []
    for user, followed_affected_ids in per_user.values():
        d = Digest(user_email=user.email)
        for tid in followed_affected_ids:
            games = team_to_games[tid]
            d.affected_teams.append(
                AffectedTeam(
                    team_id=tid,
                    team_name=teams_by_id[tid].name,
                    missed_calls=sum(g.bad_calls for g in games),
                    games=games,
                )
            )
        digests.append((user, d))

    return digests


def deliver_digest(user: User, digest: Digest) -> None:
    """Log the digest that would be sent. Replace with SES SendEmail in Phase 10 —
    signature stays identical; only the body changes."""
    print()
    print("=" * 78)
    print(f"[DIGEST → {user.email}]  (Phase 10: swap this for SES.SendEmail)")
    print("=" * 78)

    total_calls = sum(t.missed_calls for t in digest.affected_teams)
    total_games = sum(len(t.games) for t in digest.affected_teams)
    plural_calls = "" if total_calls == 1 else "s"
    plural_games = "" if total_games == 1 else "s"
    plural_teams = "" if len(digest.affected_teams) == 1 else "s"

    print(
        f"\nHi — the L2M report flagged {total_calls} missed call{plural_calls} "
        f"across {total_games} game{plural_games} your followed "
        f"team{plural_teams} played in."
    )
    print(
        "(This means a missed call *happened in* a game your team played "
        "in — L2M doesn't tell us which side benefited.)"
    )

    for team in digest.affected_teams:
        call_word = "call" if team.missed_calls == 1 else "calls"
        print(f"\n{team.team_name} — {team.missed_calls} missed {call_word}:")
        for g in team.games:
            g_word = "call" if g.bad_calls == 1 else "calls"
            print(
                f"  • {g.date}  {g.matchup}  "
                f"({g.bad_calls} bad {g_word}, game_id={g.game_id})"
            )


def main() -> None:
    game_ids = sys.argv[1:] if len(sys.argv) > 1 else None

    with SessionLocal() as session:
        scope = (
            f"{len(game_ids)} game_id(s) from CLI"
            if game_ids
            else "every L2M-reported game in the DB"
        )
        print(f"Computing digests across {scope} ...")

        results = compute_digests(session, game_ids)

        if not results:
            print(
                "\nNo digests to deliver — no user with digest_enabled=true "
                "follows any team involved in these games' IC/INC calls."
            )
            return

        print(f"\n{len(results)} digest(s) to deliver.")
        for user, digest in results:
            deliver_digest(user, digest)


if __name__ == "__main__":
    main()
