"""Read-only PDF metadata and embedded text-layer inspection."""

from __future__ import annotations

from pathlib import Path
from statistics import fmean

import fitz
from pydantic import BaseModel


class PdfInspection(BaseModel):
    path: str
    filename: str
    size_bytes: int
    page_count: int
    metadata: dict[str, str]
    metadata_only: bool
    has_text_layer: bool | None = None
    text_characters_total: int | None = None
    text_characters_min: int | None = None
    text_characters_max: int | None = None
    text_characters_average: float | None = None
    per_page_text_characters: list[int] | None = None


def _page_text_char_count(page: fitz.Page) -> int:
    """Count embedded text characters without OCR or image rendering."""

    return len(page.get_text("text") or "")


def inspect_pdf(path: str | Path, *, analyze_text_layer: bool = True) -> PdfInspection:
    """Inspect a PDF without modifying it, rendering pages, OCR, or network access."""

    pdf_path = Path(path).expanduser().resolve(strict=False)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if not pdf_path.is_file():
        raise ValueError(f"PDF path is not a file: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"File is not a PDF: {pdf_path}")

    size_bytes = pdf_path.stat().st_size
    with fitz.open(pdf_path) as document:
        page_count = document.page_count
        metadata = {
            str(key).replace("\ufffd", "?"): str(value).replace("\ufffd", "?")
            for key, value in document.metadata.items()
            if value not in (None, "")
        }
        counts: list[int] | None = None
        if analyze_text_layer:
            counts = [_page_text_char_count(page) for page in document]

    if counts is None:
        return PdfInspection(
            path=str(pdf_path),
            filename=pdf_path.name,
            size_bytes=size_bytes,
            page_count=page_count,
            metadata=metadata,
            metadata_only=True,
        )

    return PdfInspection(
        path=str(pdf_path),
        filename=pdf_path.name,
        size_bytes=size_bytes,
        page_count=page_count,
        metadata=metadata,
        metadata_only=False,
        has_text_layer=any(count > 0 for count in counts),
        text_characters_total=sum(counts),
        text_characters_min=min(counts, default=0),
        text_characters_max=max(counts, default=0),
        text_characters_average=round(fmean(counts), 2) if counts else 0.0,
        per_page_text_characters=counts,
    )
