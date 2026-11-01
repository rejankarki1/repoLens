"""add user repositories

Workspace membership: which repositories each user has added. Repository data stays shared. If the local
development user (local@repolens.local) exists, it becomes a member of every existing repository so that data
imported before authentication stays reachable; it is never assigned to a GitHub user automatically (see
docs/auth-migration.md for the explicit claim command). A database without that user gets no rows.

Revision ID: d2b8f4e0a513
Revises: c1a7e3d9f402
Create Date: 2026-10-31 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = 'd2b8f4e0a513'
down_revision = 'c1a7e3d9f402'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_repositories',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'repository_id'),
    )
    op.create_index('ix_user_repositories_repository_id', 'user_repositories', ['repository_id'])
    op.execute(
        """
        INSERT INTO user_repositories (user_id, repository_id)
        SELECT u.id, r.id FROM users u CROSS JOIN repositories r
        WHERE u.email = 'local@repolens.local'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index('ix_user_repositories_repository_id', table_name='user_repositories')
    op.drop_table('user_repositories')
