"""Page-level text quality gate and capability-based OCR routing.

The gate never treats the mere presence of a PDF text layer as proof that the
text is suitable for publication.  It produces stable, serializable evidence
that the production pipeline can persist and expose to the desktop UI.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

from .io_utils import atomic_write_json, atomic_write_jsonl
from .provider_registry import ProviderRegistry, parse_model_json


@dataclass(frozen=True)
class PageTextQualityResult:
    page_id: str
    extractor: str
    quality_score: float
    passed: bool
    issue_codes: tuple[str, ...]
    metrics: dict[str, Any]
    recommended_route: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["issue_codes"] = list(self.issue_codes)
        return value


@dataclass(frozen=True)
class OCRRouteResult:
    page_id: str
    route: str
    status: str
    text: str
    provider_id: str | None
    issue_codes: tuple[str, ...]
    artifact_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["issue_codes"] = list(self.issue_codes)
        return value


class PageTextQualityGate:
    """Deterministic heuristic gate with explainable page metrics."""

    def evaluate(self, *, page_id: str, text: str, extractor: str = "pymupdf",
                 geometry: dict[str, Any] | None = None) -> PageTextQualityResult:
        value = text.replace("\r\n", "\n").replace("\r", "\n")
        total = max(1, len(value))
        printable = sum(char.isprintable() or char in "\n\t" for char in value)
        controls = sum(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value)
        replacement = value.count("\ufffd")
        alnum = sum(char.isalnum() for char in value)
        whitespace = sum(char.isspace() for char in value)
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        duplicates = len(lines) - len(set(lines))
        repeated_runs = len(re.findall(r"(.)\1{7,}", value, flags=re.DOTALL))
        broken_words = len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]-\s*\n\s*[A-Za-zÀ-ÖØ-öø-ÿ]", value))
        long_unspaced = max((len(token) for token in re.split(r"\s+", value)), default=0)
        geometry = dict(geometry or {})
        block_count = int(geometry.get("block_count", 0) or 0)
        columns = int(geometry.get("estimated_columns", 1) or 1)
        image_coverage = float(geometry.get("image_coverage", 0.0) or 0.0)

        metrics = {
            "text_characters": len(value.strip()),
            "printable_ratio": round(printable / total, 4),
            "control_ratio": round(controls / total, 4),
            "replacement_ratio": round(replacement / total, 4),
            "alnum_ratio": round(alnum / total, 4),
            "whitespace_ratio": round(whitespace / total, 4),
            "duplicate_line_ratio": round(duplicates / max(1, len(lines)), 4),
            "repeated_character_runs": repeated_runs,
            "hyphenated_line_breaks": broken_words,
            "longest_unspaced_token": long_unspaced,
            "block_count": block_count,
            "estimated_columns": columns,
            "image_coverage": round(image_coverage, 4),
        }
        issues: list[str] = []
        score = 1.0
        chars = metrics["text_characters"]
        if chars == 0:
            issues.append("empty_text"); score -= 1.0
        elif chars < 40:
            issues.append("too_few_characters"); score -= 0.45
        if metrics["printable_ratio"] < 0.94:
            issues.append("low_printable_ratio"); score -= 0.35
        if metrics["replacement_ratio"] > 0.002:
            issues.append("unicode_replacement_characters"); score -= 0.4
        if metrics["control_ratio"] > 0.002:
            issues.append("control_characters"); score -= 0.25
        if metrics["alnum_ratio"] < 0.25 and chars >= 40:
            issues.append("glyph_mapping_or_language_mismatch"); score -= 0.3
        if metrics["duplicate_line_ratio"] > 0.35 and len(lines) >= 6:
            issues.append("repeated_lines"); score -= 0.25
        if repeated_runs:
            issues.append("repeated_characters"); score -= min(0.3, repeated_runs * 0.1)
        if long_unspaced > 180:
            issues.append("missing_spaces_or_font_mapping"); score -= 0.25
        if columns > 1:
            issues.append("multi_column_reading_order_risk"); score -= 0.12
        if image_coverage > 0.82 and chars < 120:
            issues.append("visual_coverage_text_mismatch"); score -= 0.3
        # Hyphenated line endings and a few repeated headers are recorded for
        # deterministic reflow; alone they do not force paid OCR.
        if broken_words:
            issues.append("hyphenation_reflow_required")
        passed = score >= 0.62 and not any(code in issues for code in (
            "empty_text", "unicode_replacement_characters", "low_printable_ratio",
            "glyph_mapping_or_language_mismatch", "missing_spaces_or_font_mapping",
        ))
        recommended = "python_text" if passed else "ocr_router"
        return PageTextQualityResult(page_id, extractor, round(max(0.0, min(1.0, score)), 4),
                                     passed, tuple(issues), metrics, recommended)


class OCRRouter:
    """Route failed text pages without pretending unavailable OCR exists."""

    def __init__(self, *, registry: ProviderRegistry | None = None,
                 vision_provider_id: str | None = None, allow_provider_calls: bool = False,
                 attempt_ledger_path: Path | None = None,
                 attempt_context: dict[str, Any] | None = None) -> None:
        self.registry = registry
        self.vision_provider_id = vision_provider_id
        self.allow_provider_calls = allow_provider_calls
        self.attempt_ledger_path = attempt_ledger_path
        self.attempt_context = dict(attempt_context or {})

    @staticmethod
    def local_capabilities() -> dict[str, bool]:
        return {
            "umi_ocr": bool(shutil.which("Umi-OCR") or shutil.which("umi-ocr")),
            "kraken": bool(shutil.which("kraken")),
        }

    def _vision_profile(self) -> Any | None:
        if self.registry is None:
            return None
        try:
            profile = self.registry.get(self.vision_provider_id, "ocr")
        except (KeyError, ValueError):
            return None
        return profile

    def route(self, *, page: fitz.Page, page_id: str, extracted_text: str,
              quality: PageTextQualityResult, output_dir: Path) -> OCRRouteResult:
        if quality.passed:
            return OCRRouteResult(page_id, "python_text", "accepted", extracted_text, None,
                                  quality.issue_codes)
        profile = self._vision_profile()
        if profile is not None and profile.provider_type == "mock":
            # Mock proves routing/state semantics only. Never relabel the
            # original extraction as OCR output.
            status = "review_required" if extracted_text.strip() else "unresolved"
            return OCRRouteResult(page_id, "mock_vision_review", status, extracted_text, profile.provider_id,
                                  quality.issue_codes)
        if profile is not None and self.allow_provider_calls:
            output_dir.mkdir(parents=True, exist_ok=True)
            image_path = output_dir / f"{page_id}.png"
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            pixmap.save(image_path)
            try:
                raw = self.registry.client(profile).vision_json(
                    prompt=("Transcribe this single book page in reading order. Preserve headings, paragraphs, "
                            "footnotes, captions and table cells. In text, put blank lines between semantic blocks; "
                            "represent tables as valid Markdown with one row per line and the same column count. "
                            "Flatten spanning or multilevel table headers into one unambiguous header row so every "
                            "header and data row has exactly the same number of cells. "
                            "Preserve reference symbols such as *, dagger, and double dagger exactly both at the "
                            "reference and the note. Return JSON with keys text, confidence, review_required and "
                            "issue_codes. Do not translate."),
                    image_path=image_path, attempt_ledger_path=self.attempt_ledger_path,
                    attempt_context={**self.attempt_context, "page_or_segment_id": page_id,
                                     "purpose": "page_text_quality_vision_ocr"},
                )
                parsed = parse_model_json(raw)
                text = str(parsed.get("text") or "").strip()
                if text:
                    return OCRRouteResult(page_id, "glm_vision", "accepted", text, profile.provider_id,
                                          tuple(str(x) for x in parsed.get("issue_codes") or ()), str(image_path))
            except Exception:
                # A failed optional visual OCR request must not destroy a book
                # that still has usable extracted text.  Preserve the page for
                # explicit Web Assist review and expose the failure as a stable
                # route issue instead of silently accepting it as OCR output.
                return OCRRouteResult(page_id, "difficult_page_web_assist", "review_required", extracted_text,
                                      profile.provider_id, quality.issue_codes + ("vision_provider_failed",),
                                      str(image_path))
        return OCRRouteResult(page_id, "difficult_page_web_assist", "review_required", extracted_text,
                              getattr(profile, "provider_id", None), quality.issue_codes)


def analyze_pdf_pages(pdf_path: Path, output_dir: Path, *, registry: ProviderRegistry | None = None,
                      vision_provider_id: str | None = None,
                      allow_provider_calls: bool = False,
                      attempt_ledger_path: Path | None = None,
                      attempt_context: dict[str, Any] | None = None) -> dict[str, Any]:
    gate = PageTextQualityGate()
    router = OCRRouter(registry=registry, vision_provider_id=vision_provider_id,
                       allow_provider_calls=allow_provider_calls, attempt_ledger_path=attempt_ledger_path,
                       attempt_context=attempt_context)
    quality_records: list[dict[str, Any]] = []
    route_records: list[dict[str, Any]] = []
    selected_text: dict[int, str] = {}
    document = fitz.open(pdf_path)
    try:
        for index, page in enumerate(document, 1):
            text = page.get_text("text") or ""
            blocks = page.get_text("blocks")
            left = sum(1 for block in blocks if float(block[0]) < page.rect.width * 0.48)
            right = sum(1 for block in blocks if float(block[0]) > page.rect.width * 0.48)
            estimated_columns = 2 if left >= 2 and right >= 2 else 1
            image_area = 0.0
            for image in page.get_images(full=True):
                try:
                    for rect in page.get_image_rects(image[0]):
                        image_area += max(0.0, rect.width * rect.height)
                except ValueError:
                    continue
            geometry = {"block_count": len(blocks), "estimated_columns": estimated_columns,
                        "image_coverage": min(1.0, image_area / max(1.0, page.rect.width * page.rect.height))}
            page_id = f"page-{index:04d}"
            quality = gate.evaluate(page_id=page_id, text=text, geometry=geometry)
            routed = router.route(page=page, page_id=page_id, extracted_text=text,
                                  quality=quality, output_dir=output_dir / "ocr_pages")
            quality_records.append(quality.to_dict())
            route_records.append(routed.to_dict())
            selected_text[index] = routed.text
    finally:
        document.close()
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_jsonl(output_dir / "page_text_quality.jsonl", quality_records)
    atomic_write_jsonl(output_dir / "ocr_routes.jsonl", route_records)
    summary = {
        "schema_version": "bookflow-page-intake-v1",
        "page_count": len(quality_records),
        "quality_passed": sum(bool(item["passed"]) for item in quality_records),
        "quality_failed": sum(not bool(item["passed"]) for item in quality_records),
        "routes": {name: sum(item["route"] == name for item in route_records)
                   for name in sorted({item["route"] for item in route_records})},
        "review_pages": [index + 1 for index, item in enumerate(route_records)
                         if item["status"] != "accepted"],
        "local_ocr_capabilities": router.local_capabilities(),
    }
    atomic_write_json(output_dir / "page_intake_summary.json", summary)
    return {**summary, "selected_text": selected_text, "quality_records": quality_records,
            "route_records": route_records}
