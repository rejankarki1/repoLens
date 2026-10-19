"""Enable pgvector for later embedding storage.

Revision ID: 0002_pgvector
Revises: 0001_foundation
"""

from alembic import op


revision = "0002_pgvector"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
