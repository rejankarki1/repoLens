"""add auth sessions

Server-side sessions for GitHub sign-in (only the SHA-256 of the cookie value is stored), GitHub profile columns
on github_accounts, and a nullable users.email (GitHub sign-in requests no email scope). Purely additive: no
existing row is changed or removed.

Revision ID: c1a7e3d9f402
Revises: b84f2c6d1e07
Create Date: 2026-10-31 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = 'c1a7e3d9f402'
down_revision = 'b84f2c6d1e07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('users', 'email', existing_type=sa.String(length=320), nullable=True)
    op.add_column('github_accounts', sa.Column('display_name', sa.String(length=255), nullable=True))
    op.add_column('github_accounts', sa.Column('avatar_url', sa.String(length=1024), nullable=True))
    op.add_column(
        'github_accounts',
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.add_column('github_accounts', sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('csrf_token', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_sessions_user_id', table_name='sessions')
    op.drop_table('sessions')
    op.drop_column('github_accounts', 'last_login_at')
    op.drop_column('github_accounts', 'created_at')
    op.drop_column('github_accounts', 'avatar_url')
    op.drop_column('github_accounts', 'display_name')
    # Fails if a signed-in user without an email exists; those rows are real data and are not deleted here.
    op.alter_column('users', 'email', existing_type=sa.String(length=320), nullable=False)
