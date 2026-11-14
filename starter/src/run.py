"""Build and verify the A/B pgvector indexes used by strategies A, B and C."""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter

from dotenv import load_dotenv
from psycopg import Error as DatabaseError

from .chunking import by_paragraph, fixed_size, write_chunk_export
from .embeddings import QUERY_INSTRUCTION, OllamaEmbedder
from .models import Chunk, DocumentPage
from .store import PgVectorStore

STARTER = Path(__file__).resolve().parents[1]
RESULTS = STARTER / "results"
FIXED_SIZE = 500
FIXED_OVERLAP = 50
PARAGRAPH_MAX_SIZE = 500
BATCH_SIZE = 16
SMOKE_QUERY = "Trong phiếu yêu cầu cung cấp dữ liệu, ghi cách nhận hồ sơ ở mục nào?"
SMOKE_FILTER = {"section": "phu_luc", "form_number": "02"}
WARMUP_TEXT = "Bảo vệ dữ liệu cá nhân."
logger = logging.getLogger(__name__)


def _load_pages() -> tuple[list[DocumentPage], str]:
    content = (RESULTS / "corpus_pages.jsonl").read_bytes()
    pages = [DocumentPage(**json.loads(line)) for line in content.splitlines() if line.strip()]
    if len({(page.document_id, page.page) for page in pages}) != len(pages):
        raise ValueError("Corpus contains duplicate pages")
    if len(pages) < 30 or any(not page.text.strip() for page in pages):
        raise ValueError("Prepare the corpus first. It must contain at least 30 nonempty pages.")
    return pages, hashlib.sha256(content).hexdigest()


def _build_chunks(pages: list[DocumentPage]) -> tuple[dict[str, list[Chunk]], dict[str, float]]:
    start = perf_counter()
    fixed = [chunk for page in pages for chunk in fixed_size(page, FIXED_SIZE, FIXED_OVERLAP)]
    fixed_seconds = perf_counter() - start
    start = perf_counter()
    structured = [chunk for page in pages for chunk in by_paragraph(page, PARAGRAPH_MAX_SIZE)]
    return {"A": fixed, "B": structured}, {"A": fixed_seconds, "B": perf_counter() - start}


def _smoke_test(store: PgVectorStore, embedder: OllamaEmbedder) -> dict:
    vector = embedder.embed_query(SMOKE_QUERY)
    hits_a = store.search("A", vector)
    hits_b = store.search("B", vector)
    hits_c = store.search("C", vector, filters=SMOKE_FILTER)
    if len(hits_a) != 3 or len(hits_b) != 3 or len(hits_c) != 3:
        raise RuntimeError("Expected three results from each strategy in the smoke test")
    if any(hit["metadata"].get(key) != value for hit in hits_c for key, value in SMOKE_FILTER.items()):
        raise RuntimeError("Strategy C returned results outside the requested form")
    if store.search("C", vector) != hits_b:
        raise RuntimeError("Strategy C without filters must return the same results as B")
    return {
        "question": SMOKE_QUERY,
        "filter_c": SMOKE_FILTER,
        "top_3": {"A": hits_a, "B": hits_b, "C": hits_c},
        "c_without_filter_matches_b": True,
    }


def _write_json(name: str, value: dict) -> None:
    target = RESULTS / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def ingest(verify_reingest: bool) -> None:
    load_dotenv(STARTER / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("Set DATABASE_URL before running ingestion")

    pages, corpus_digest = _load_pages()
    configurations, chunk_seconds = _build_chunks(pages)
    embedder = OllamaEmbedder(os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    stats = {}
    vectors = {}
    with PgVectorStore(database_url, embedder.dimensions, embedder.digest) as store:
        versions = store.versions()
        store.create_schema()
        logger.info("Warming up %s before timing ingest", embedder.model_name)
        start = perf_counter()
        embedder.embed_documents([WARMUP_TEXT])
        warmup_seconds = perf_counter() - start
        for strategy, chunks in configurations.items():
            logger.info("Embedding %s: %d chunks", strategy, len(chunks))
            start = perf_counter()
            texts = [chunk.text for chunk in chunks]
            embeddings = []
            for offset in range(0, len(texts), BATCH_SIZE):
                embeddings.extend(embedder.embed_documents(texts[offset:offset + BATCH_SIZE]))
            vectors[strategy] = embeddings
            lengths = list(map(len, texts))
            stats[strategy] = {
                "chunk_count": len(chunks),
                "characters_total": sum(lengths),
                "characters_per_chunk": {"min": min(lengths), "median": median(lengths), "max": max(lengths)},
                "chunk_seconds": round(chunk_seconds[strategy], 6),
                "embedding_seconds": round(perf_counter() - start, 6),
                "api_cost": 0,
            }

        with store.connection.transaction():
            for strategy, chunks in configurations.items():
                start = perf_counter()
                store.upsert(strategy, chunks, vectors[strategy])
                stats[strategy]["upsert_seconds"] = round(perf_counter() - start, 6)
                stats[strategy]["ingest_seconds"] = round(sum(
                    stats[strategy][metric] for metric in ("chunk_seconds", "embedding_seconds", "upsert_seconds")
                ), 6)
        counts = store.counts()
        expected = {strategy: len(chunks) for strategy, chunks in configurations.items()}
        if counts != expected:
            raise RuntimeError(f"Unexpected pgvector counts: {counts}, expected: {expected}")

        reingest = None
        if verify_reingest:
            with store.connection.transaction():
                for strategy, chunks in configurations.items():
                    store.upsert(strategy, chunks, vectors[strategy])
            repeated_counts = store.counts()
            if repeated_counts != counts:
                raise RuntimeError(f"Row counts changed on repeat upsert: {counts} → {repeated_counts}")
            reingest = {"before": counts, "after": repeated_counts, "reused_vectors": True}
        smoke = _smoke_test(store, embedder)

    stats["C"] = {
        "reuses_index": "B",
        "chunk_count": stats["B"]["chunk_count"],
        "characters_total": stats["B"]["characters_total"],
        "characters_per_chunk": stats["B"]["characters_per_chunk"],
        "additional_ingest_seconds": 0,
        "api_cost": 0,
    }
    write_chunk_export(RESULTS / "chunks.jsonl", configurations)
    _write_json("ingestion_report.json", {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "corpus_sha256": corpus_digest,
        "page_count": len(pages),
        "chunking": {
            "A": {"size": FIXED_SIZE, "overlap": FIXED_OVERLAP},
            "B": {"max_size": PARAGRAPH_MAX_SIZE},
        },
        "embedding": {
            "source_model": "Qwen/Qwen3-Embedding-0.6B",
            "provider": "Ollama local",
            "model": embedder.model_name,
            "model_digest": embedder.digest,
            "quantization": embedder.quantization,
            "ollama_version": embedder.version,
            "dimensions": embedder.dimensions,
            "batch_size": BATCH_SIZE,
            "warmup_seconds": round(warmup_seconds, 6),
            "query_instruction": QUERY_INSTRUCTION,
        },
        "storage": {**versions, "table": "day6_chunks", "metric": "cosine", "search": "exact"},
        "strategies": stats,
        "reingest": reingest,
        "smoke_test": smoke,
    })
    logger.info("Ingestion OK. Stored %s. C reuses B. See results/ingestion_report.json.", counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-reingest", action="store_true", help="Repeat upsert and check row counts.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(RESULTS / "ingestion.log", encoding="utf-8")])
    try:
        ingest(args.verify_reingest)
    except (DatabaseError, OSError, RuntimeError, ValueError) as error:
        logger.error("%s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
