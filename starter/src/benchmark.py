"""Run frozen dev/test retrieval or apply evidence review to saved traces."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from statistics import median
from time import perf_counter

from dotenv import load_dotenv

from .embeddings import QUERY_INSTRUCTION, OllamaEmbedder
from .evaluation import STRATEGIES, evaluate_query, select_strategy, summarize
from .run import RESULTS, STARTER, WARMUP_TEXT, _write_json
from .store import PgVectorStore

BENCHMARK = STARTER.parent / "benchmark" / "questions.jsonl"
TIMING_REPEATS = 3


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path, rows):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _snapshot() -> dict:
    report = json.loads((RESULTS / "ingestion_report.json").read_text(encoding="utf-8"))
    corpus_digest = hashlib.sha256((RESULTS / "corpus_pages.jsonl").read_bytes()).hexdigest()
    if report["corpus_sha256"] != corpus_digest:
        raise RuntimeError("Corpus changed since ingestion. Rebuild the index before benchmarking.")
    return {
        "benchmark_sha256": hashlib.sha256(BENCHMARK.read_bytes()).hexdigest(),
        "corpus_sha256": corpus_digest,
        "chunks_sha256": hashlib.sha256((RESULTS / "chunks.jsonl").read_bytes()).hexdigest(),
        "ingestion_completed_at": report["completed_at"],
        "chunking": report["chunking"],
        "model_digest": report["embedding"]["model_digest"],
        "query_instruction": QUERY_INSTRUCTION,
        "top_k": 3,
    }


def _retrieve(questions: list[dict], snapshot: dict) -> list[dict]:
    load_dotenv(STARTER / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("Set DATABASE_URL before benchmarking.")
    embedder = OllamaEmbedder(os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    if embedder.digest != snapshot["model_digest"]:
        raise RuntimeError("The Ollama model does not match the stored embeddings.")
    embedder.embed_documents([WARMUP_TEXT])

    traces = []
    expected = {
        (row["strategy"], row["chunk_id"]): row
        for row in _read_jsonl(RESULTS / "chunks.jsonl") if row["strategy"] in {"A", "B"}
    }
    with PgVectorStore(database_url, embedder.dimensions, embedder.digest) as store:
        with store.connection.transaction():
            store.connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            indexed = store.connection.execute("""
                SELECT strategy, chunk_id, document_id, source, page, text, metadata
                FROM day6_chunks WHERE model_digest = %s
            """, (embedder.digest,)).fetchall()
            actual = {(row["strategy"], row["chunk_id"]): row for row in indexed}
            if actual != expected:
                raise RuntimeError("Stored chunks differ from the exported index. Run ingestion again.")
            for index, question in enumerate(questions):
                vector = embedder.embed_query(question["question"])
                filters = {s: question.get("filter", {}) if s == "C" else {} for s in STRATEGIES}
                times = {s: [] for s in STRATEGIES}
                hits = {}
                for repeat in range(TIMING_REPEATS):
                    start_index = (index + repeat) % len(STRATEGIES)
                    for strategy in STRATEGIES[start_index:] + STRATEGIES[:start_index]:
                        start = perf_counter()
                        result = store.search(strategy, vector, top_k=snapshot["top_k"], filters=filters[strategy])
                        times[strategy].append((perf_counter() - start) * 1000)
                        hits[strategy] = result
                if not filters["C"] and hits["B"] != hits["C"]:
                    raise RuntimeError(f"B/C differ on unfiltered question {question['id']}")
                for strategy in STRATEGIES:
                    traces.append({
                        "strategy": strategy,
                        "question": question,
                        "applied_filter": filters[strategy],
                        "retrieval_ms": median(times[strategy]),
                        "retrieval_samples_ms": times[strategy],
                        **evaluate_query(question, hits[strategy]),
                    })
    return traces


def _apply_reviews(traces: list[dict], split: str) -> list[dict]:
    path = RESULTS / f"evidence_review_{split}.jsonl"
    if not path.exists():
        raise RuntimeError(f"Record reviewed alternatives in {path.name} before scoring.")
    notes = {}
    retrieved = {(row["question"]["id"], hit["chunk_id"]) for row in traces for hit in row["hits"]}
    for row in _read_jsonl(path):
        key = row["question_id"], row["chunk_id"]
        if key not in retrieved or key in notes or not row["reason"].strip():
            raise ValueError(f"Invalid reviewed evidence entry: {key}")
        notes[key] = row["reason"]

    return [
        {
            **trace,
            **evaluate_query(
                trace["question"], trace["hits"],
                {chunk_id: reason for (qid, chunk_id), reason in notes.items() if qid == trace["question"]["id"]},
            ),
        }
        for trace in traces
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("split", choices=("dev", "test"))
    parser.add_argument("--score", action="store_true", help="Score saved retrieval with reviewed alternatives.")
    args = parser.parse_args()
    split = args.split
    raw_path = RESULTS / f"retrieval_{split}.jsonl"
    latest = _snapshot()
    if split == "test":
        selection_file = "dev_selection.json" if args.score else "benchmark_dev.json"
        dev = json.loads((RESULTS / selection_file).read_text(encoding="utf-8"))
        if dev["snapshot"] != latest:
            raise RuntimeError("Corpus, benchmark or embedding configuration changed after dev selection.")
    if args.score:
        recorded = json.loads((RESULTS / f"retrieval_{split}_run.json").read_text(encoding="utf-8"))
        if recorded["snapshot"] != latest:
            raise RuntimeError("Benchmark input changed after retrieval.")
        traces = _apply_reviews(_read_jsonl(raw_path), split)
        _write_jsonl(RESULTS / f"trace_{split}.jsonl", traces)
        summary = summarize(traces)
        selected = select_strategy(summary) if split == "dev" else dev["selected_on_dev"]
        _write_json(f"benchmark_{split}.json", {
            **recorded, "scored_at": datetime.now(timezone.utc).isoformat(),
            "selected_on_dev": selected, "strategies": summary,
        })
        print(json.dumps({"split": split, "selected_on_dev": selected, "strategies": summary}, indent=2))
        return

    questions = [q for q in _read_jsonl(BENCHMARK) if q["split"] == split]
    if split == "test":
        _write_json("dev_selection.json", dev)
    traces = _retrieve(questions, latest)
    _write_jsonl(raw_path, traces)
    _write_json(f"retrieval_{split}_run.json", {
        "split": split, "retrieved_at": datetime.now(timezone.utc).isoformat(), "snapshot": latest,
    })
    print(json.dumps({"split": split, "initial_gold_quote_matches": summarize(traces)}, indent=2))


if __name__ == "__main__":
    main()
