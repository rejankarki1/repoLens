"""add showcase answers

Curated, real Ask AI answers (with citations) that signed-out visitors can read for showcase repositories.
Empty until `python -m apps.api.scripts.publish_showcase_answers` copies saved answers in.

Revision ID: a9e3c5d17b02
Revises: f1c4a7b9e820
Create Date: 2026-11-10 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a9e3c5d17b02'
down_revision = 'f1c4a7b9e820'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'showcase_answers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('citations', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_showcase_answers_repository_id', 'showcase_answers', ['repository_id'])


def downgrade() -> None:
    op.drop_index('ix_showcase_answers_repository_id', table_name='showcase_answers')
    op.drop_table('showcase_answers')
