"""
Game-level trigger gate — "is this game worth running the AI Verdict engine on?"

Implements the provisional rule from the AI Verdict Engine spec section
("Discussion spike trigger, resolved"): a game passes if, over the comments
that have a resolved `published_at`, it has BOTH

  - at least MIN_POSITIVE_COMMENTS triage-classifier-positive comments, AND
  - a positive rate of at least MIN_POSITIVE_RATE

Both numbers were calibrated on only two games (0022500696 confirmed-controversy
Draymond/JT-Orr, 0022400020 routine MIN@SAC) and sit on the recall-favoring
side of the two data points, same tradeoff as the triage classifier's 0.4
threshold — a missed real controversy fails silently; a false trigger just
costs one extra LLM pass. Expect these to move once more games go through it.

Positives/rate are read from Qdrant payload (`is_relevant` was stamped at
index time with the same triage classifier used elsewhere) so the gate stays
consistent with retrieval: whatever Qdrant considers relevant is what gets
counted here.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client.http import models as qm

from app.retrieval.qdrant import COLLECTION, get_client


MIN_POSITIVE_COMMENTS = 50
MIN_POSITIVE_RATE = 0.07


@dataclass(frozen=True)
class TriggerResult:
    game_id: str
    total_timestamped: int   # comments with a resolved published_at
    positives: int           # triage-classifier-positive subset of the above
    rate: float              # positives / total_timestamped (0 if denom is 0)
    passes: bool

    def __str__(self) -> str:
        return (f"{self.game_id}  pos={self.positives:>4}  "
                f"total={self.total_timestamped:>4}  "
                f"rate={self.rate*100:5.2f}%  "
                f"{'PASS' if self.passes else 'FAIL'}")


def _count(client, must_filters: list[qm.FieldCondition]) -> int:
    return client.count(
        collection_name=COLLECTION,
        count_filter=qm.Filter(must=must_filters),
        exact=True,
    ).count


def _timestamped_filter(game_id: str) -> list[qm.FieldCondition]:
    """game_id == <game_id> AND published_at IS NOT NULL."""
    return [
        qm.FieldCondition(key="game_id", match=qm.MatchValue(value=game_id)),
        qm.FieldCondition(key="published_at", is_null=qm.IsNullCondition(
            is_null=qm.PayloadField(key="published_at")
        )),
    ]


def game_passes_trigger(
    game_id: str,
    min_positive: int = MIN_POSITIVE_COMMENTS,
    min_rate: float = MIN_POSITIVE_RATE,
) -> TriggerResult:
    """Check the trigger rule for one game. Returns counts + rate + decision."""
    client = get_client()

    # Qdrant doesn't support "field IS NOT NULL" directly, so we count the
    # whole-game total and the is_null==true subset, and subtract.
    game_filter = [qm.FieldCondition(key="game_id",
                                     match=qm.MatchValue(value=game_id))]
    total_all = _count(client, game_filter)

    is_null_filter = game_filter + [qm.IsNullCondition(
        is_null=qm.PayloadField(key="published_at")
    )]
    total_null = client.count(
        collection_name=COLLECTION,
        count_filter=qm.Filter(must=is_null_filter),
        exact=True,
    ).count
    total_timestamped = total_all - total_null

    positives_filter = game_filter + [
        qm.FieldCondition(key="is_relevant", match=qm.MatchValue(value=True)),
    ]
    positives_total = _count(client, positives_filter)

    # Positives on timestamped-only rows: same subtraction trick.
    pos_null_filter = positives_filter + [qm.IsNullCondition(
        is_null=qm.PayloadField(key="published_at")
    )]
    pos_null = client.count(
        collection_name=COLLECTION,
        count_filter=qm.Filter(must=pos_null_filter),
        exact=True,
    ).count
    positives = positives_total - pos_null

    rate = positives / total_timestamped if total_timestamped else 0.0
    passes = positives >= min_positive and rate >= min_rate
    return TriggerResult(
        game_id=game_id,
        total_timestamped=total_timestamped,
        positives=positives,
        rate=rate,
        passes=passes,
    )
