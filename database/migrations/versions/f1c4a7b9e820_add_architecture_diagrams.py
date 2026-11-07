"""add architecture diagrams

One row per repository holding the AI-identified functional-component diagram (Architecture Diagram V2):
the status of the latest generation attempt, and the last completed result (commit, generator version, the
deterministic analyzer version it was built from, the sealed diagram JSON). Mirrors architecture_analyses'
shape exactly; the result columns are only written when an attempt succeeds, so a failed or lost
re-generation never erases the previous result.

Revision ID: f1c4a7b9e820
Revises: d2b8f4e0a513
Create Date: 2026-11-04 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f1c4a7b9e820'
down_revision = 'd2b8f4e0a513'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'architecture_diagrams',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('target_commit_sha', sa.String(length=40), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('result_commit_sha', sa.String(length=40), nullable=True),
        sa.Column('generator_version', sa.Integer(), nullable=True),
        sa.Column('source_analyzer_version', sa.Integer(), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.Column('diagram', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name='valid_architecture_diagram_status'),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repository_id', name='uq_architecture_diagrams_repository'),
    )


def downgrade() -> None:
    op.drop_table('architecture_diagrams')
