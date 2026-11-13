"""PostgreSQL + pgvector store for RAG chunks.

The vector dimension is supplied by the embedding model, so this store works
with EmbeddingGemma (768 dimensions by default) as well as other models.
"""
from __future__ import annotations

import math
from typing import Any

from .models import Chunk


class PgVectorStore:
    """Persist chunks and search them with cosine similarity.

    Each strategy (A/B/C) has a separate namespace in the same table.
    `upsert` expects the complete chunk set for the documents in the batch:
    it removes the previous rows for those documents and inserts the new set
    in one transaction, preventing stale chunks after rechunking.
    """

    def __init__(self, database_url: str, dimensions: int) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database_url must be a non-empty PostgreSQL URL")
        if (
            not isinstance(dimensions, int)
            or isinstance(dimensions, bool)
            or dimensions <= 0
        ):
            raise ValueError("dimensions must be a positive integer")

        self.database_url = database_url.strip()
        self.dimensions = dimensions

    def _connect(self):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependencies. Install psycopg[binary] and pgvector "
                "from requirements.txt."
            ) from exc

        conn = psycopg.connect(self.database_url)
        register_vector(conn)
        return conn

    def create_schema(self) -> None:
        """Create pgvector extension, storage table and supporting indexes."""
        statements = [
            "CREATE EXTENSION IF NOT EXISTS vector",
            f"""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                strategy TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                source TEXT NOT NULL,
                page INTEGER NOT NULL,
                content TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                embedding vector({self.dimensions}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (strategy, chunk_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS rag_chunks_metadata_gin
            ON rag_chunks USING GIN (metadata)
            """,
            """
            CREATE INDEX IF NOT EXISTS rag_chunks_strategy_idx
            ON rag_chunks (strategy)
            """,
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)

    def upsert(
        self,
        strategy: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> None:
        """Replace chunks for supplied documents without retaining stale rows."""
        if not isinstance(strategy, str) or not strategy.strip():
            raise ValueError("strategy must be a non-empty string")
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not chunks:
            return

        try:
            from pgvector import Vector
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependencies. Install psycopg[binary] and pgvector."
            ) from exc

        document_ids: set[str] = set()
        seen_chunk_ids: set[str] = set()
        rows = []

        for chunk, vector in zip(chunks, vectors):
            if not isinstance(chunk, Chunk):
                raise TypeError("Every item in chunks must be a Chunk.")
            if not chunk.chunk_id or not chunk.document_id or not chunk.source:
                raise ValueError("chunk_id, document_id and source are required.")
            if chunk.chunk_id in seen_chunk_ids:
                raise ValueError(f"Duplicate chunk_id in input: {chunk.chunk_id}")
            seen_chunk_ids.add(chunk.chunk_id)

            if len(vector) != self.dimensions:
                raise ValueError(
                    f"Vector for {chunk.chunk_id} has {len(vector)} dimensions; "
                    f"expected {self.dimensions}."
                )
            try:
                numeric_vector = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Vector for {chunk.chunk_id} is not numeric.") from exc
            if not all(math.isfinite(value) for value in numeric_vector):
                raise ValueError(f"Vector for {chunk.chunk_id} contains NaN or infinity.")

            metadata = dict(chunk.metadata or {})
            metadata.update({
                "document_id": chunk.document_id,
                "source": chunk.source,
                "page": chunk.page,
            })
            document_ids.add(chunk.document_id)
            rows.append((
                strategy.strip(),
                chunk.chunk_id,
                chunk.document_id,
                chunk.source,
                chunk.page,
                chunk.text,
                Jsonb(metadata),
                Vector(numeric_vector),
            ))

        insert_sql = """
            INSERT INTO rag_chunks
                (strategy, chunk_id, document_id, source, page,
                 content, metadata, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                document_id = EXCLUDED.document_id,
                source = EXCLUDED.source,
                page = EXCLUDED.page,
                content = EXCLUDED.content,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                updated_at = NOW()
        """

        # DELETE + INSERT are in the same transaction. If insertion fails,
        # PostgreSQL rolls back and preserves the previous document version.
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM rag_chunks
                    WHERE strategy = %s AND document_id = ANY(%s)
                    """,
                    (strategy.strip(), list(document_ids)),
                )
                cur.executemany(insert_sql, rows)

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search by cosine similarity; optionally constrain JSONB metadata."""
        if not isinstance(strategy, str) or not strategy.strip():
            raise ValueError("strategy must be a non-empty string")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        if len(query_vector) != self.dimensions:
            raise ValueError(
                f"Query vector has {len(query_vector)} dimensions; "
                f"expected {self.dimensions}."
            )
        try:
            numeric_query = [float(value) for value in query_vector]
        except (TypeError, ValueError) as exc:
            raise ValueError("query_vector must contain only numeric values.") from exc
        if not all(math.isfinite(value) for value in numeric_query):
            raise ValueError("query_vector contains NaN or infinity")
        if filters is not None and not isinstance(filters, dict):
            raise TypeError("filters must be a dictionary or None")

        try:
            from pgvector import Vector
        except ImportError as exc:
            raise RuntimeError(
                "Missing pgvector dependency. Install requirements.txt."
            ) from exc

        where = ["strategy = %s"]
        params: list[Any] = [Vector(numeric_query), strategy.strip()]
        if filters:
            # JSONB containment supports exact metadata matching, e.g.
            # {"page": 12} or {"section": "Introduction"}.
            where.append("metadata @> %s::jsonb")
            import json
            params.append(json.dumps(filters, ensure_ascii=False))

        sql = f"""
            SELECT chunk_id, document_id, source, page, content, metadata,
                   1 - (embedding <=> %s) AS score
            FROM rag_chunks
            WHERE {' AND '.join(where)}
            ORDER BY embedding <=> %s
            LIMIT %s
        """
        params.extend([Vector(numeric_query), top_k])

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        return [
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "source": source,
                "page": page,
                "text": content,
                "score": float(score),
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
            for chunk_id, document_id, source, page, content, metadata, score in rows
        ]
