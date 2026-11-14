"""Persist chunk snapshots and search by exact cosine similarity in pgvector."""

from math import isfinite
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import Chunk

FILTER_FIELDS = {"section", "form_number"}


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int, model_digest: str) -> None:
        if not 1 <= dimensions <= 16000:
            raise ValueError("Invalid pgvector dimensions")
        self.dimensions = dimensions
        self.model_digest = model_digest
        self.connection = psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row)

    def __enter__(self) -> "PgVectorStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.connection.close()

    def create_schema(self) -> None:
        """Create the two physical indexes used by the three strategies."""
        with self.connection.transaction():
            self.connection.execute(f"""
                CREATE TABLE IF NOT EXISTS day6_chunks (
                    strategy text NOT NULL CHECK (strategy IN ('A', 'B')),
                    chunk_id text NOT NULL,
                    document_id text NOT NULL,
                    source text NOT NULL,
                    page integer NOT NULL CHECK (page > 0),
                    text text NOT NULL,
                    metadata jsonb NOT NULL,
                    model_digest text NOT NULL,
                    embedding vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )
            """)

    def _vector_value(self, vector: list[float]) -> str:
        if len(vector) != self.dimensions or not all(isfinite(value) for value in vector) or not any(vector):
            raise ValueError(f"Expected a finite nonzero {self.dimensions}-dimensional vector")
        return "[" + ",".join(map(str, vector)) + "]"

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Replace the snapshot for each incoming document in one transaction."""
        if strategy not in {"A", "B"}:
            raise ValueError("Only A and B store chunks. C searches B.")
        if not chunks or len(chunks) != len(vectors):
            raise ValueError("Expected one vector per chunk and at least one chunk")
        ids_by_document: dict[str, list[str]] = {}
        rows = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            ids_by_document.setdefault(chunk.document_id, []).append(chunk.chunk_id)
            rows.append((
                strategy, chunk.chunk_id, chunk.document_id, chunk.source, chunk.page,
                chunk.text, Jsonb(chunk.metadata), self.model_digest, self._vector_value(vector),
            ))
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("Duplicate chunk IDs in snapshot")

        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("LOCK TABLE day6_chunks IN SHARE ROW EXCLUSIVE MODE")
            cursor.executemany("""
                INSERT INTO day6_chunks (
                    strategy, chunk_id, document_id, source, page, text, metadata, model_digest, embedding
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                    source = EXCLUDED.source,
                    page = EXCLUDED.page,
                    text = EXCLUDED.text,
                    metadata = EXCLUDED.metadata,
                    model_digest = EXCLUDED.model_digest,
                    embedding = EXCLUDED.embedding
            """, rows)
            for document_id, ids in ids_by_document.items():
                cursor.execute("""
                    DELETE FROM day6_chunks
                    WHERE strategy = %s AND document_id = %s AND NOT (chunk_id = ANY(%s))
                """, (strategy, document_id, ids))

    def counts(self) -> dict[str, int]:
        with self.connection.transaction():
            rows = self.connection.execute("""
                SELECT strategy, count(*) AS count FROM day6_chunks
                WHERE model_digest = %s GROUP BY strategy
            """, (self.model_digest,)).fetchall()
        return {row["strategy"]: row["count"] for row in rows}

    def versions(self) -> dict[str, str]:
        with self.connection.transaction():
            row = self.connection.execute("""
                SELECT current_setting('server_version') AS postgres, extversion AS pgvector
                FROM pg_extension WHERE extname = 'vector'
            """).fetchone()
        if row is None:
            raise RuntimeError("The vector extension is missing from this database")
        return row

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Search all of A/B or the metadata-matched subset of B for C."""
        if strategy not in {"A", "B", "C"} or top_k < 1:
            raise ValueError("Use strategy A, B or C with a positive top_k")
        filters = filters or {}
        if filters.keys() - FILTER_FIELDS or any(
            not isinstance(value, str) or not value for value in filters.values()
        ):
            raise ValueError("Only nonempty string filters for section and form_number are supported")
        if strategy != "C" and filters:
            raise ValueError("Only strategy C accepts metadata filters")

        group = "B" if strategy == "C" else strategy
        with self.connection.transaction():
            return self.connection.execute("""
                SELECT chunk_id, document_id, source, page, text, metadata,
                       1 - (embedding <=> %s::vector) AS score
                FROM day6_chunks
                WHERE strategy = %s AND model_digest = %s AND metadata @> %s
                ORDER BY score DESC, chunk_id
                LIMIT %s
            """, (self._vector_value(query_vector), group, self.model_digest, Jsonb(filters), top_k)).fetchall()
