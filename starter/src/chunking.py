"""Create chunks with exact offsets into the normalized source page."""

from bisect import bisect_right
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from .models import Chunk, DocumentPage


def _chunk(page: DocumentPage, method: str, start: int, end: int) -> Chunk:
    structure = [
        span["metadata"]
        for span in page.metadata.get("spans", [])
        if span["start"] < end and start < span["end"]
    ]
    metadata = {
        key: page.metadata[key]
        for key in ("printed_page", "section", "form_number")
        if key in page.metadata
    }
    metadata.update(start=start, end=end)
    if articles := sorted({item["article_number"] for item in structure if "article_number" in item}):
        metadata["article_numbers"] = articles
    text = page.text[start:end]
    digest = sha256(text.encode("utf-8")).hexdigest()[:16]
    chunk_id = f"{method}:{page.document_id}:{page.page}:{start}:{end}:{digest}"
    return Chunk(chunk_id, page.document_id, page.source, page.page, text, metadata)


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Slide fixed-size character windows within the page."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("Expected size > overlap >= 0")
    chunks = []
    for start in range(0, len(page.text), size - overlap):
        end = min(start + size, len(page.text))
        chunks.append(_chunk(page, "A", start, end))
        if end == len(page.text):
            break
    return chunks


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Keep each Điều or form separate and prefer paragraph boundaries."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")
    if not page.text:
        return []

    boundaries = [0]
    previous_article = None
    for span in page.metadata.get("spans", []):
        article = span["metadata"].get("article_number")
        if article != previous_article and article is not None and span["start"]:
            boundaries.append(span["start"])
        previous_article = article
    boundaries.append(len(page.text))

    paragraph_ends = [i + 1 for i, char in enumerate(page.text) if char == "\n"]
    chunks = []
    for start, stop in zip(boundaries, boundaries[1:]):
        while start < stop:
            end = min(start + max_size, stop)
            if end < stop:
                paragraph = bisect_right(paragraph_ends, end) - 1
                if paragraph >= 0 and paragraph_ends[paragraph] > start:
                    end = paragraph_ends[paragraph]
                else:
                    space = page.text.rfind(" ", start, end)
                    if space > start:
                        end = space + 1
            chunks.append(_chunk(page, "B", start, end))
            start = end
    return chunks


def write_chunk_export(path: Path, configurations: dict[str, list[Chunk]]) -> None:
    """Show all three strategies in the export, with C pointing to B's stored chunks."""
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for strategy in ("A", "B", "C"):
            storage_strategy = "B" if strategy == "C" else strategy
            for chunk in configurations[storage_strategy]:
                row = {"strategy": strategy, **asdict(chunk)}
                if strategy == "C":
                    row["storage_strategy"] = storage_strategy
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)
