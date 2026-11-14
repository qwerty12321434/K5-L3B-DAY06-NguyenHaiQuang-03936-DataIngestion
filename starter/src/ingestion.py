"""Extract PDF pages and prepare the Nghị định 13/2023 corpus."""

import argparse
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pypdf
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .models import DocumentPage

DOCUMENT_ID = "nd-13-2023-nd-cp"
PDF_FILENAME = "2023_685 + 686_13-2023-NĐ-CP.pdf"
SOURCE_URL = "https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-13-2023-nd-cp-39228.htm"
GAZETTE_HEADER = re.compile(
    r"(?:(?P<left>\d+)\s+)?CÔNG BÁO/Số 685 \+ 686/Ngày 30-4-2023"
    r"(?:\s+(?P<right>\d+))?"
)
CHAPTER = re.compile(r"Chương ([IVX]+)$")
SUBSECTION = re.compile(r"Mục (\d+)$")
ARTICLE = re.compile(r"Điều (\d+)\.\s")
CLAUSE = re.compile(r"(\d+)\.\s")
POINT = re.compile(r"([a-zđ])\)\s")
FORM = re.compile(r"Mẫu số (\d{2})$")
logger = logging.getLogger(__name__)


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract one layout-text record for every physical page, using 1-based numbering."""
    pages = []
    for number, pdf_page in enumerate(PdfReader(path).pages, start=1):
        metadata = {"status": "ok"}
        try:
            text = pdf_page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False)
        except (PdfReadError, ValueError, NotImplementedError) as error:
            text = ""
            metadata.update(status="error", message=str(error))

        if metadata["status"] == "ok" and not text.strip():
            metadata.update(status="empty", message="No extractable text")
        if metadata["status"] != "ok":
            logger.warning("PDF page %d: %s", number, metadata["message"])
        pages.append(DocumentPage(document_id, source, number, text, metadata))
    return pages


def normalize_text(text: str) -> str:
    """Join wrapped PDF lines while keeping indented paragraphs and list items."""
    paragraphs: list[str] = []
    current = ""
    for raw_line in unicodedata.normalize("NFC", text).splitlines():
        line = " ".join(raw_line.split())
        if not line or raw_line[0].isspace():
            if current:
                paragraphs.append(current)
            current = line
        else:
            current = f"{current} {line}".strip()
    if current:
        paragraphs.append(current)
    return "\n".join(paragraphs)


def clean_gazette_page(page: DocumentPage) -> None:
    """Remove page furniture and known extraction artifacts from this edition."""
    lines = page.text.splitlines()
    header = GAZETTE_HEADER.fullmatch(lines[0].strip()) if lines else None
    printed_number = (header["left"] or header["right"]) if header else None
    if printed_number:
        page.metadata["printed_page"] = int(printed_number)
        lines.pop(0)
    else:
        page.metadata["printed_page"] = None
        page.metadata["header_warning"] = "Printed page number not found"
        logger.warning("PDF page %d: printed page number not found", page.page)

    corrections = {
        29: (("aky]", ""),),  # The printed signature block has no such line.
        33: (("..................110 gửi", "..................¹ gửi"),),
        35: (("..................111 gửi", "..................¹ gửi"),),
        36: (
            ("THAY ĐỔI NỘI DUNG HỒ SƠ......1                             12", "THAY ĐỔI NỘI DUNG HỒ SƠ......¹"),
            ("..................213 gửi", "..................² gửi"),
        ),
        37: (("..................114 gửi", "..................¹ gửi"),),
    }
    text = "\n".join(lines)
    for original, replacement in corrections.get(page.page, ()):
        if original in text:
            text = text.replace(original, replacement)
            page.metadata.setdefault("corrections", []).append(original)

    # Wide gaps in this PDF separate side-by-side fields, such as form options a) and b).
    layout_lines = []
    for line in text.splitlines():
        indentation = line[:len(line) - len(line.lstrip())]
        layout_lines.append(indentation + re.sub(r" {5,}", "\n    ", line.lstrip()))
    page.text = normalize_text("\n".join(layout_lines))

    if page.metadata["status"] == "ok" and not page.text:
        page.metadata.update(status="empty", message="No text remains after cleaning")
        logger.warning("PDF page %d: no text remains after cleaning", page.page)


def tag_structure(pages: list[DocumentPage]) -> None:
    """Assign metadata to ranges of each page, carrying headings across page breaks."""
    context: dict[str, str | int] = {}
    in_appendix = False
    for page in pages:
        section = "phu_luc" if page.page >= 30 else "than_nghi_dinh"
        if section == "phu_luc" and not in_appendix:
            context.clear()
            in_appendix = True
        page.metadata.update(section=section, spans=[])
        if page.page == 30:  # The form directory is excluded from retrieval.
            continue

        offset = 0
        spans = page.metadata["spans"]
        for paragraph in page.text.split("\n") if page.text else ():
            if section == "than_nghi_dinh":
                if match := CHAPTER.fullmatch(paragraph):
                    context = {"chapter": match[1]}
                elif match := SUBSECTION.fullmatch(paragraph):
                    chapter = context.get("chapter")
                    context = {"subsection": int(match[1])}
                    if chapter is not None:
                        context["chapter"] = chapter
                elif match := ARTICLE.match(paragraph):
                    context.pop("clause_number", None)
                    context.pop("point", None)
                    context["article_number"] = int(match[1])
                elif "article_number" in context:
                    if match := CLAUSE.match(paragraph):
                        context["clause_number"] = int(match[1])
                        context.pop("point", None)
                    elif match := POINT.match(paragraph):
                        context["point"] = match[1]
                if paragraph == "TM. CHÍNH PHỦ":
                    context.clear()
            elif match := FORM.fullmatch(paragraph):
                context = {"form_number": match[1]}

            end = offset + len(paragraph)
            if spans and spans[-1]["metadata"] == context:
                spans[-1]["end"] = end
            else:
                spans.append({"start": offset, "end": end, "metadata": context.copy()})
            offset = end + 1

        article_numbers = list(dict.fromkeys(
            span["metadata"]["article_number"]
            for span in spans if "article_number" in span["metadata"]
        ))
        if article_numbers:
            page.metadata["article_numbers"] = article_numbers
        if section == "phu_luc" and "form_number" in context:
            page.metadata["form_number"] = context["form_number"]


def prepare_corpus(pdf_path: Path, repo_root: Path, accessed_at: date) -> dict:
    """Write the manifest, and export selected clean pages once all checks pass."""
    pages = load_pdf(pdf_path, DOCUMENT_ID, SOURCE_URL)
    for page in pages:
        clean_gazette_page(page)
    tag_structure(pages)

    usable = [
        page for page in pages if page.page != 30 and page.metadata["status"] == "ok"
    ]
    manifest = {
        "source_id": DOCUMENT_ID,
        "title": "Nghị định số 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân",
        "source_path_or_url": SOURCE_URL,
        "local_path": pdf_path.relative_to(repo_root).as_posix(),
        "publication": "Công báo số 685 + 686, ngày 30-04-2023",
        "accessed_at": accessed_at.isoformat(),
        "license_or_permission": (
            "Bản Công báo được công bố công khai. Dùng bản tải về cho bài thực hành. "
            "Chưa xác minh quyền tái phân phối bản PDF; cung cấp liên kết Công báo cho người chấm."
        ),
        "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "parser": {"name": "pypdf", "version": pypdf.__version__, "mode": "layout"},
        "physical_page_count": len(pages),
        "content_page_count": len(usable),
        "page_count_rule": (
            "Tính trang PDF 1–29 (thân Nghị định) và 31–37 (nội dung biểu mẫu). "
            "Trang 30 là danh mục biểu mẫu. Trang rỗng hoặc lỗi trích xuất không được tính."
        ),
        "pages": [
            {
                "page": page.page,
                "printed_page": page.metadata["printed_page"],
                "section": page.metadata["section"],
                "included": page.page != 30 and page.metadata["status"] == "ok",
                "normalized_characters": len(page.text),
                **{key: page.metadata[key] for key in (
                    "status", "message", "header_warning", "corrections", "article_numbers", "form_number"
                ) if key in page.metadata},
            }
            for page in pages
        ],
    }
    (repo_root / "data" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if len(pages) != 37 or len(usable) != 36 or any(
        page.metadata["printed_page"] != page.page + 2 for page in pages
    ):
        raise ValueError("Corpus differs from the expected edition. See data/manifest.json.")

    output = repo_root / "starter" / "results" / "corpus_pages.jsonl"
    output.write_text("".join(
        json.dumps(asdict(page), ensure_ascii=False) + "\n" for page in usable
    ), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accessed-at", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    repo_root = Path(__file__).resolve().parents[2]
    pdf_path = repo_root / "starter" / "data" / PDF_FILENAME
    manifest = prepare_corpus(pdf_path, repo_root, args.accessed_at)
    print(f"Exported {manifest['content_page_count']}/{manifest['physical_page_count']} PDF pages.")
    print("Manifest: data/manifest.json")
    print("Clean pages: starter/results/corpus_pages.jsonl")


if __name__ == "__main__":
    main()
