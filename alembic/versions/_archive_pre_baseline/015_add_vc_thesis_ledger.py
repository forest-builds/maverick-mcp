"""Add VC loop thesis ledger

Revision ID: 015_add_vc_thesis_ledger
Revises: 014_add_portfolio_models
Create Date: 2026-06-01 12:00:00.000000

This migration adds the thesis ledger for the VC loop feature. Each row records
an investment thesis proposed by the loop: the rationale, normalized signal
inputs, a conviction score, per-component score breakdown, status, and (later)
the realized outcome used by the learning loop.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "015_add_vc_thesis_ledger"
down_revision = "014_add_portfolio_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the vc_thesis_ledger table."""
    op.create_table(
        "vc_thesis_ledger",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("thesis_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("thesis", sa.Text, nullable=False),
        sa.Column("features", sa.JSON, nullable=True),
        sa.Column("conviction", sa.Float, nullable=False),
        sa.Column("score_breakdown", sa.JSON, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column("proposed_action", sa.Text, nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("outcome", sa.JSON, nullable=True),
        sa.Column("note_path", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index("idx_vc_thesis_ticker", "vc_thesis_ledger", ["ticker"])
    op.create_index("idx_vc_thesis_status", "vc_thesis_ledger", ["status"])


def downgrade() -> None:
    """Drop the vc_thesis_ledger table."""
    op.drop_index("idx_vc_thesis_status", table_name="vc_thesis_ledger")
    op.drop_index("idx_vc_thesis_ticker", table_name="vc_thesis_ledger")
    op.drop_table("vc_thesis_ledger")
