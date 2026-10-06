"""ai_verdicts table — game-level verdict (reframed from per-play, 2026-10-06)

Revision ID: c7d2a4e8f91a
Revises: 1173b060f3c8
Create Date: 2026-10-06

Game-level `ai_verdicts` landing after the Phase 7 rescue experiment settled
that play-specific discussion mostly doesn't exist in the YouTube corpus (see
spec AI Verdict Engine section). The per-play design (`referee_id`,
`l2m_call_id`) is intentionally not created — game-level L2M validation is
computed at read time from `l2m_calls` aggregates, not stored on the verdict.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c7d2a4e8f91a"
down_revision: Union[str, Sequence[str], None] = "1173b060f3c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VERDICT_CATEGORY_VALUES = ("no_concern", "contested", "officiating_controversy")


def upgrade() -> None:
    verdict_category = postgresql.ENUM(
        *VERDICT_CATEGORY_VALUES, name="verdict_category"
    )
    verdict_category.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ai_verdicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.String(), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(
                *VERDICT_CATEGORY_VALUES,
                name="verdict_category",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("justification_text", sa.Text(), nullable=False),
        sa.Column(
            "evidence_comment_ids",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("n_comments_considered", sa.Integer(), nullable=False),
        sa.Column("n_triage_positive", sa.Integer(), nullable=False),
        sa.Column("positive_rate", sa.Float(), nullable=False),
        sa.Column(
            "mentioned_referee_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=True,
        ),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.UniqueConstraint(
            "game_id", "model_name", "prompt_version",
            name="uq_ai_verdicts_game_model_prompt",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_ai_verdicts_confidence_range",
        ),
    )
    op.create_index(
        op.f("ix_ai_verdicts_game_id"), "ai_verdicts", ["game_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_verdicts_game_id"), table_name="ai_verdicts")
    op.drop_table("ai_verdicts")
    postgresql.ENUM(
        *VERDICT_CATEGORY_VALUES, name="verdict_category"
    ).drop(op.get_bind(), checkfirst=True)
