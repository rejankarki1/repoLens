"""add message citations

Structured citations (real file paths and line ranges of the retrieved chunks) stored with each
assistant answer. Existing messages get an empty list.

Revision ID: 7a3d9e5b2c41
Revises: 11c177d30e38
Create Date: 2026-10-28 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '7a3d9e5b2c41'
down_revision = '11c177d30e38'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'messages',
        sa.Column('citations', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('messages', 'citations')
