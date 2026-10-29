"""add code search index

Full-text search over code chunks (a weighted tsvector column with a GIN index, backfilled for chunks
that already exist) and a content-addressed table of embeddings for semantic search.

Revision ID: 11c177d30e38
Revises: cac673a06c6a
Create Date: 2026-10-26 12:30:44.988752
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '11c177d30e38'
down_revision = 'cac673a06c6a'
branch_labels = None
depends_on = None

# Same normalization as apps/api/services/code_search.py (path and symbol names weighted A, code weighted C).
# Written out here so this revision keeps working even if that code changes later.
BACKFILL_SEARCH_VECTOR = r"""
UPDATE code_chunks AS c
SET search_vector = d.vec
FROM (
    SELECT c2.id,
           setweight(to_tsvector('simple', m.meta || ' ' || regexp_replace(regexp_replace(m.meta, '([a-z0-9])([A-Z])', '\1 \2', 'g'), '([A-Z]+)([A-Z][a-z])', '\1 \2', 'g')), 'A')
           || setweight(to_tsvector('simple', b.body || ' ' || regexp_replace(regexp_replace(b.body, '([a-z0-9])([A-Z])', '\1 \2', 'g'), '([A-Z]+)([A-Z][a-z])', '\1 \2', 'g')), 'C') AS vec
    FROM code_chunks AS c2
    JOIN source_files AS sf ON sf.id = c2.source_file_id
    CROSS JOIN LATERAL (SELECT regexp_replace(sf.path || ' ' || coalesce((SELECT string_agg(s, ' ') FROM jsonb_array_elements_text(c2.symbols) AS s), ''), '[^A-Za-z0-9_]+', ' ', 'g') AS meta) AS m
    CROSS JOIN LATERAL (SELECT regexp_replace(c2.content, '[^A-Za-z0-9_]+', ' ', 'g') AS body) AS b
    WHERE c2.search_vector IS NULL
) AS d
WHERE c.id = d.id
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")  # already enabled by revision 0002; harmless if so

    op.add_column('code_chunks', sa.Column('search_vector', postgresql.TSVECTOR(), nullable=True))
    op.execute(BACKFILL_SEARCH_VECTOR)  # existing chunks become searchable without re-ingesting
    op.create_index('ix_code_chunks_search_vector', 'code_chunks', ['search_vector'], unique=False, postgresql_using='gin')

    # One vector per (chunk text hash, model). `vector(1536)` is gemini-embedding-001 requested at 1,536
    # dimensions; a different width needs a new column or table. No ANN index yet: searches are scoped to one repository's chunks and
    # use an exact scan, which is fast at this size (add HNSW if a repository grows past ~100k chunks).
    op.execute(
        """
        CREATE TABLE code_embeddings (
            content_hash VARCHAR(64) NOT NULL,
            model VARCHAR(64) NOT NULL,
            embedding VECTOR(1536) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (content_hash, model)
        )
        """
    )


def downgrade() -> None:
    op.drop_table('code_embeddings')
    op.drop_index('ix_code_chunks_search_vector', table_name='code_chunks', postgresql_using='gin')
    op.drop_column('code_chunks', 'search_vector')
