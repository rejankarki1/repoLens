"""add architecture analyses

One row per repository holding the architecture graph computed from its ingested source files: the status of
the latest attempt, and the last completed result (commit, high-level summary, file-level detail). The result
columns are only written when an attempt succeeds, so a failed re-analysis keeps the previous result.

Revision ID: b84f2c6d1e07
Revises: 7a3d9e5b2c41
Create Date: 2026-10-29 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b84f2c6d1e07'
down_revision = '7a3d9e5b2c41'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'architecture_analyses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('target_commit_sha', sa.String(length=40), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('result_commit_sha', sa.String(length=40), nullable=True),
        sa.Column('analyzer_version', sa.Integer(), nullable=True),
        sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name='valid_architecture_status'),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repository_id', name='uq_architecture_analyses_repository'),
    )


def downgrade() -> None:
    op.drop_table('architecture_analyses')
