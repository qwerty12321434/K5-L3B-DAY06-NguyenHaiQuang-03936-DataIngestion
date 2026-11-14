"""Score pgvector traces against the frozen benchmark and reviewed evidence."""

from statistics import median
from typing import Any
from unicodedata import normalize

STRATEGIES = ("A", "B", "C")


def _normalized(text: str) -> str:
    return " ".join(normalize("NFC", text).split())


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]],
                   reviewed: dict[str, str] | None = None) -> dict[str, Any]:
    """One chunk must carry sufficient evidence at a verified source location."""
    quote = _normalized(question["evidence_quote"])
    scope = question.get("filter", {})
    scored = []
    for rank, hit in enumerate(hits[:3], start=1):
        in_scope = all(hit["metadata"].get(key) == value for key, value in scope.items())
        exact = (
            hit["document_id"] == question["gold_source_id"]
            and hit["page"] == question["gold_page"]
            and quote in _normalized(hit["text"])
        )
        reason = (reviewed or {}).get(hit["chunk_id"])
        method = None
        if in_scope:
            if exact:
                method = "gold_quote"
            elif reason:
                method = "reviewed_equivalent"
        scored.append({
            **hit, "rank": rank, "in_scope": in_scope, "evidence_hit": method is not None,
            "evidence_match": method, "review_note": reason,
        })
    return {
        "hits": scored,
        "hit_at_1": bool(scored and scored[0]["evidence_hit"]),
        "hit_at_3": any(hit["evidence_hit"] for hit in scored),
        "filter_compliant": (bool(scored) and all(hit["in_scope"] for hit in scored)) if scope else None,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts and pgvector latency for every strategy in one split."""
    summary = {}
    for strategy in STRATEGIES:
        group = [trace for trace in traces if trace["strategy"] == strategy]
        filtered = [trace for trace in group if trace["filter_compliant"] is not None]
        summary[strategy] = {
            "queries": len(group),
            "hit_at_1": sum(trace["hit_at_1"] for trace in group),
            "hit_at_3": sum(trace["hit_at_3"] for trace in group),
            "retrieval_median_ms": median(trace["retrieval_ms"] for trace in group),
            "filter_compliant": sum(trace["filter_compliant"] for trace in filtered),
            "filter_queries": len(filtered),
        }
    return summary


def select_strategy(summary: dict[str, Any]) -> str:
    """Apply the dev-only selection rule frozen in benchmark/README.md."""
    return min(STRATEGIES, key=lambda strategy: (
        -summary[strategy]["hit_at_3"], -summary[strategy]["hit_at_1"],
        summary[strategy]["retrieval_median_ms"], strategy,
    ))
