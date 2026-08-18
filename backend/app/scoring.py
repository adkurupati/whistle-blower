"""
Official Score computation — crew-level attribution from L2M ratings.

Every referee on game_officials for a game "owns" that game's L2M calls
(we can't attribute individual calls to individual crew members from
the L2M endpoint alone). Score is the shrunk correct rate using a
league-average prior, so refs with tiny sample sizes don't top the
ranking on noise.
"""

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import GameOfficial, L2MCall, L2MReport, Referee

CORRECT_CODES = ("CC", "CNC")
GRADED_CODES = ("CC", "CNC", "IC", "INC")
DEFAULT_PRIOR_WEIGHT = 20  # equivalent-observation weight on the league prior


@dataclass
class RefereeScore:
    rank: int
    referee_id: int
    name: str
    total_calls_graded: int
    correct_calls: int
    raw_correct_rate: float
    shrunk_rate: float


def _correct_expr():
    return case((L2MCall.call_rating.in_(CORRECT_CODES), 1), else_=0)


def league_average_correct_rate(session: Session) -> float:
    """Correct-call rate across every graded call in l2m_calls."""
    correct, total = session.execute(
        select(
            func.coalesce(func.sum(_correct_expr()), 0),
            func.count(),
        ).where(L2MCall.call_rating.in_(GRADED_CODES))
    ).one()
    return float(correct) / total if total else 0.0


def compute_rankings(
    session: Session, prior_weight: int = DEFAULT_PRIOR_WEIGHT
) -> list[RefereeScore]:
    """Return every ref with at least 1 graded call, sorted by shrunk_rate desc."""
    league_avg = league_average_correct_rate(session)

    rows = session.execute(
        select(
            Referee.id,
            Referee.name,
            func.coalesce(func.sum(_correct_expr()), 0).label("correct"),
            func.count().label("total"),
        )
        .select_from(Referee)
        .join(GameOfficial, GameOfficial.referee_id == Referee.id)
        .join(L2MReport, L2MReport.game_id == GameOfficial.game_id)
        .join(L2MCall, L2MCall.l2m_report_id == L2MReport.id)
        .where(L2MCall.call_rating.in_(GRADED_CODES))
        .group_by(Referee.id, Referee.name)
        .having(func.count() >= 1)
    ).all()

    scored = []
    for ref_id, name, correct, total in rows:
        correct = int(correct)
        total = int(total)
        raw = correct / total
        shrunk = (correct + prior_weight * league_avg) / (total + prior_weight)
        scored.append(
            RefereeScore(
                rank=0,
                referee_id=ref_id,
                name=name,
                total_calls_graded=total,
                correct_calls=correct,
                raw_correct_rate=raw,
                shrunk_rate=shrunk,
            )
        )

    scored.sort(key=lambda r: r.shrunk_rate, reverse=True)
    for i, r in enumerate(scored, 1):
        r.rank = i
    return scored
