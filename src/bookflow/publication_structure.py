"""Generic, durable VLM-assisted whole-book structure analysis."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file
from .multilingual_workspace import _jsonl, _load, _save
from .provider_registry import ProviderRegistry, parse_model_json


PAGE_CLASSES = {"cover", "copyright", "toc", "body", "appendix", "index", "blank", "other"}
VISUAL_OBJECT_TYPES = {"photo", "map", "illustration", "diagram", "table"}
SEMANTIC_REGION_TYPES = {"header", "footer", "footnote"}
PROMPT_VERSION = "generic-publication-structure-v4-anchored-regions"
SCHEMA_VERSION = "bookflow-structure-v4"
NORMALIZER_VERSION = "anchored-region-normalizer-v4"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bbox_overlap_ratio(left: list[float], right: list[float]) -> float:
    if len(left) != 4 or len(right) != 4:
        return 0.0
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max((left[2] - left[0]) * (left[3] - left[1]), 1e-9)
    return intersection / area


def _append(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _atomic_raw(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    temporary.replace(path)


def mechanical_preflight(workspace: Path) -> dict[str, Any]:
    manifest = _load(workspace)
    pdf_path = Path(manifest["source_pdf"])
    if sha256_file(pdf_path) != manifest["source_pdf_sha256"]:
        raise ValueError("source PDF SHA-256 mismatch")
    pdf = fitz.open(pdf_path)
    records = []
    for index, page in enumerate(pdf):
        text = page.get_text("text") or ""
        blocks = page.get_text("blocks")
        drawings = page.get_drawings()
        words = page.get_text("words")
        text_dict = page.get_text("dict")
        image_count = len(page.get_images(full=True))
        width, height = float(page.rect.width), float(page.rect.height)
        page_area = max(float(page.rect.get_area()), 1.0)
        candidate_visual_regions = []
        seen_regions: set[tuple[int, int, int, int, int]] = set()
        for image in page.get_images(full=True):
            xref = int(image[0])
            for raw_rect in page.get_image_rects(xref):
                rect = raw_rect & page.rect
                if rect.is_empty or rect.is_infinite:
                    continue
                area_ratio = float(rect.get_area() / page_area)
                width_ratio = float(rect.width / page.rect.width)
                height_ratio = float(rect.height / page.rect.height)
                if area_ratio >= 0.80 or (width_ratio >= 0.92 and height_ratio >= 0.92):
                    continue
                if area_ratio < 0.0025 or rect.width < 24 or rect.height < 24:
                    continue
                key = (xref, round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
                if key in seen_regions:
                    continue
                seen_regions.add(key)
                candidate_visual_regions.append({
                    "source": "pdf_embedded_image",
                    "source_xref": xref,
                    "bbox": [round(rect.x0 / width, 6), round(rect.y0 / height, 6),
                             round(rect.x1 / width, 6), round(rect.y1 / height, 6)],
                    "area_ratio": round(area_ratio, 6),
                })
        text_regions: list[dict[str, Any]] = []
        page_font_sizes: list[float] = []
        for raw_block in text_dict.get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            lines: list[str] = []
            sizes: list[float] = []
            for line in raw_block.get("lines", []):
                spans = line.get("spans", [])
                value = "".join(str(span.get("text") or "") for span in spans).strip()
                if value:
                    lines.append(value)
                sizes.extend(float(span.get("size") or 0) for span in spans if span.get("size"))
            block_text = "\n".join(lines).strip()
            block_rect = fitz.Rect(raw_block.get("bbox", (0, 0, 0, 0))) & page.rect
            if not block_text or block_rect.is_empty:
                continue
            average_size = sum(sizes) / len(sizes) if sizes else 0.0
            page_font_sizes.extend(sizes)
            text_regions.append({"text": block_text,
                                 "bbox": [round(block_rect.x0 / width, 6), round(block_rect.y0 / height, 6),
                                          round(block_rect.x1 / width, 6), round(block_rect.y1 / height, 6)],
                                 "font_size": average_size})
        median_font_size = (sorted(page_font_sizes)[len(page_font_sizes) // 2]
                            if page_font_sizes else 0.0)
        candidate_semantic_regions: list[dict[str, Any]] = []
        page_marker = re.compile(r"^\s*(?:\d{1,4}|[ivxlcdm]{1,8})[.!]?\s*$", re.I)
        for region in text_regions:
            bbox = region["bbox"]; block_text = str(region["text"])
            region_type = None
            if bbox[1] <= 0.08 and not page_marker.fullmatch(block_text):
                region_type = "header"
            elif (bbox[1] >= 0.68 and median_font_size and region["font_size"] <= median_font_size * 0.86
                  and re.match(r"^(?:[*\u2020\u2021]|\(?\d{1,3}[.)])\s*", block_text)):
                region_type = "footnote"
            elif bbox[3] >= 0.92 and not page_marker.fullmatch(block_text):
                region_type = "footer"
            if region_type:
                candidate_semantic_regions.append({"type": region_type, "bbox": bbox,
                                                   "source": "pdf_text_geometry", "confidence": 0.8})
        records.append({
            "physical_page": index + 1, "width": width, "height": height,
            "rotation": int(page.rotation), "landscape": width > height,
            "text_characters": len(text), "word_count": len(words), "block_count": len(blocks),
            "image_count": image_count, "drawing_count": len(drawings),
            "candidate_visual_region_count": len(candidate_visual_regions),
            "candidate_visual_regions": candidate_visual_regions,
            "candidate_semantic_region_count": len(candidate_semantic_regions),
            "candidate_semantic_regions": candidate_semantic_regions,
            "textless": not bool(text.strip()),
            "mechanical_high_risk": bool(page.rotation or width > height or not text.strip()
                                         or candidate_visual_regions or candidate_semantic_regions
                                         or len(drawings) > 40),
        })
    pdf.close()
    path = workspace / "data/mechanical_preflight.jsonl"
    atomic_write_jsonl(path, records)
    result = {"pages": len(records), "high_risk_pages": [x["physical_page"] for x in records if x["mechanical_high_risk"]],
              "path": str(path), "source_pdf_sha256": manifest["source_pdf_sha256"]}
    atomic_write_json(workspace / "data/mechanical_preflight_summary.json", result)
    return result


def build_deterministic_structure_workspace(workspace: Path) -> dict[str, Any]:
    """Build a conservative Python-owned structure for pages that do not need VLM.

    Complex/high-risk pages remain review items.  This gives ordinary text PDFs
    a real structure/segmentation plan without paying for or inventing VLM
    results, while preserving the same Phase 13.6 publication artifacts.
    """
    workspace = workspace.resolve()
    manifest = _load(workspace)
    preflight_path = workspace / "data/mechanical_preflight.jsonl"
    if not preflight_path.is_file():
        mechanical_preflight(workspace)
    preflight = _jsonl(preflight_path)
    document = fitz.open(Path(manifest["source_pdf"]))
    records: list[dict[str, Any]] = []
    try:
        for item in preflight:
            page_no = int(item["physical_page"])
            text = document[page_no - 1].get_text("text").strip()
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            lower = first_line.casefold()
            page_class = "cover" if page_no == 1 and len(text) < 2500 else "body"
            if re.search(r"\b(table of contents|contents|sommaire|inhaltsverzeichnis|índice)\b", lower):
                page_class = "toc"
            elif re.search(r"\b(index|bibliography|references)\b", lower):
                page_class = "index" if "index" in lower else "appendix"
            elif not text:
                page_class = "blank"
            heading = bool(first_line and len(first_line) <= 120 and (
                first_line.isupper() or re.match(r"^(chapter|part|section|appendix)\b", first_line, re.I)))
            high_risk = bool(item.get("mechanical_high_risk"))
            raw = {
                "page_class": page_class,
                "chapter_boundary": heading,
                "header": any(region.get("type") == "header" for region in item.get("candidate_semantic_regions", [])),
                "footer": any(region.get("type") == "footer" for region in item.get("candidate_semantic_regions", [])),
                "columns": None,
                "footnotes": any(region.get("type") == "footnote" for region in item.get("candidate_semantic_regions", [])),
                "images": int(item.get("candidate_visual_region_count", 0)) > 0,
                "captions": False,
                "tables": int(item.get("drawing_count", 0)) > 40,
                "landscape": bool(item.get("landscape")),
                "reading_order": ["top-to-bottom"],
                "geometry": "mechanical-preflight",
                "confidence": 0.7 if high_risk else 0.9,
                "review_required": high_risk,
                "review_reason": "mechanical_high_risk" if high_risk else None,
                "semantic_regions": item.get("candidate_semantic_regions", []),
            }
            records.append(_normalize_record(raw, page_no, item))
    finally:
        document.close()
    validated = workspace / "data/structure/validated/deterministic-python.json"
    atomic_write_json(validated, {"pages": records, "fingerprint": manifest["source_pdf_sha256"],
                                  "authority": "python_deterministic"})
    result = finalize_structure_workspace(workspace, total_batches=0,
                                          profile={"provider_id": "python_deterministic", "model": "rules-v1"})
    result.update({"provider_calls_this_run": 0, "deterministic": True})
    return result


def _contact_sheet(pdf_path: Path, pages: list[int], output: Path) -> None:
    document = fitz.open(pdf_path)
    cell_w, cell_h, columns = 520, 700, 3
    rows = math.ceil(len(pages) / columns)
    sheet = Image.new("RGB", (cell_w * columns, cell_h * rows), "#ede9df")
    draw = ImageDraw.Draw(sheet)
    for position, page_no in enumerate(pages):
        page = document[page_no - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(0.7, 0.7), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        image.thumbnail((cell_w - 24, cell_h - 42))
        x = (position % columns) * cell_w + (cell_w - image.width) // 2
        y = (position // columns) * cell_h + 30
        sheet.paste(image, (x, y))
        draw.rectangle((position % columns * cell_w, position // columns * cell_h,
                        position % columns * cell_w + cell_w - 1, position // columns * cell_h + cell_h - 1),
                       outline="#8b8173", width=2)
        draw.text((position % columns * cell_w + 10, position // columns * cell_h + 7),
                  f"PHYSICAL PAGE {page_no}", fill="#211f1b")
    document.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def _prompt(pages: list[int]) -> str:
    image_description = (
        f"This image is the complete rendered physical page {pages[0]} with no contact-sheet padding. "
        "All bbox values must be normalized against this entire image. "
        if len(pages) == 1 else
        "Analyze this contact sheet as book pages. Labels give authoritative physical page numbers. "
    )
    return (
        image_description
        + "Return one JSON object with a pages array and exactly one record for every requested page. "
        "Each record: physical_page (integer), page_class (cover|copyright|toc|body|appendix|index|blank|other), "
        "chapter_boundary (boolean), header (boolean), footer (boolean), columns (1|2|3|null), "
        "footnotes (boolean), images (boolean), captions (boolean), tables (boolean), landscape (boolean), "
        "semantic_regions (array). Each semantic region is independently translatable and uses: "
        "type (header|footer|footnote), bbox ([x0,y0,x1,y1] normalized to 0..1), confidence (0..1). "
        "A superscript citation or note-reference number by itself is not a footnote region; keep that marker with "
        "its body paragraph. Only set footnotes=true when actual note text is printed in a separate page region. "
        "Do not merge any real header, footer, or footnote text region into the body. Page numbers are not semantic regions. "
        "visual_regions (array). Each visual region must describe one meaningful photo, map, illustration, "
        "diagram, or table using: type (photo|map|illustration|diagram|table), bbox ([x0,y0,x1,y1] normalized "
        "to 0..1 within that labeled page), caption_bbox (same form or null), "
        "publication_role (body_figure|cover_art|logo|copyright_mark|ornament), and confidence (0..1). "
        "Only body_figure belongs in the translated publication. Never label a page background, scan layer, "
        "page border, institutional logo, publisher mark, copyright mark, ornament, or whole-page facsimile as body_figure. "
        "reading_order (array of short region labels), geometry (short string or null), confidence (0..1), "
        "review_required (boolean), review_reason (string or null). Do not transcribe prose and do not invent. "
        f"Required physical pages: {pages}."
    )


def _normalize_record(raw: dict[str, Any], page_no: int, mechanical: dict[str, Any]) -> dict[str, Any]:
    page_class = str(raw.get("page_class") or "other")
    confidence = raw.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    errors = []
    if page_class not in PAGE_CLASSES:
        errors.append("invalid_page_class"); page_class = "other"
    columns = raw.get("columns")
    if columns not in {1, 2, 3, None}:
        errors.append("invalid_columns"); columns = None
    reading_order = raw.get("reading_order")
    if not isinstance(reading_order, list):
        errors.append("invalid_reading_order"); reading_order = []
    semantic_regions = []
    raw_semantic_regions = raw.get("semantic_regions") or []
    if not isinstance(raw_semantic_regions, list):
        errors.append("invalid_semantic_regions"); raw_semantic_regions = []
    for index, region in enumerate(raw_semantic_regions):
        if not isinstance(region, dict) or region.get("type") not in SEMANTIC_REGION_TYPES:
            errors.append(f"invalid_semantic_region_type:{index}")
            continue
        try:
            bbox = [float(value) for value in region.get("bbox", [])]
            region_confidence = min(1.0, max(0.0, float(region.get("confidence", 0))))
        except (TypeError, ValueError):
            errors.append(f"invalid_semantic_region:{index}")
            continue
        if (len(bbox) != 4 or not all(0 <= value <= 1 for value in bbox)
                or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
            errors.append(f"invalid_semantic_bbox:{index}")
            continue
        if region["type"] == "footnote":
            if ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 0.002):
                # A tiny superscript reference marker remains part of its body
                # paragraph. It is not independently translatable footnote text.
                continue
            mechanical_footnotes = [item for item in mechanical.get("candidate_semantic_regions", [])
                                    if item.get("type") == "footnote"]
            if mechanical.get("text_characters") and not any(
                _bbox_overlap_ratio(bbox, item.get("bbox", [])) >= 0.35 for item in mechanical_footnotes
            ):
                # On text-bearing PDFs a VLM-only bottom strip is commonly the
                # last body line or a credit. Require an actual small-print note
                # block; scanned/textless pages still retain VLM-only regions.
                continue
        semantic_regions.append({"type": str(region["type"]), "bbox": bbox,
                                 "confidence": region_confidence,
                                 "source": str(region.get("source") or "vision_layout")})
    visual_regions = []
    raw_regions = raw.get("visual_regions")
    if raw_regions is None:
        raw_regions = []
    if not isinstance(raw_regions, list):
        errors.append("invalid_visual_regions"); raw_regions = []
    candidates = [item.get("bbox") for item in mechanical.get("candidate_visual_regions", [])
                  if isinstance(item, dict) and isinstance(item.get("bbox"), list) and len(item["bbox"]) == 4]

    def overlaps_candidate(bbox: list[float]) -> bool:
        if not candidates:
            return True
        for candidate in candidates:
            left, top = max(bbox[0], candidate[0]), max(bbox[1], candidate[1])
            right, bottom = min(bbox[2], candidate[2]), min(bbox[3], candidate[3])
            intersection = max(0.0, right - left) * max(0.0, bottom - top)
            bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            candidate_area = (candidate[2] - candidate[0]) * (candidate[3] - candidate[1])
            if intersection / max(min(bbox_area, candidate_area), 1e-9) >= 0.50:
                return True
        return False

    for index, region in enumerate(raw_regions):
        if not isinstance(region, dict) or region.get("type") not in VISUAL_OBJECT_TYPES:
            errors.append(f"invalid_visual_region_type:{index}")
            continue
        try:
            bbox = [float(value) for value in region.get("bbox", [])]
            region_confidence = min(1.0, max(0.0, float(region.get("confidence", 0))))
        except (TypeError, ValueError):
            errors.append(f"invalid_visual_region:{index}")
            continue
        if (len(bbox) != 4 or not all(0 <= value <= 1 for value in bbox)
                or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
            errors.append(f"invalid_visual_bbox:{index}")
            continue
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if area >= 0.80:
            errors.append(f"full_page_visual_rejected:{index}")
            continue
        if (region["type"] == "illustration" and region.get("caption_bbox") is None
                and area < 0.03 and (bbox[1] < 0.25 or bbox[3] > 0.85)):
            continue
        if not overlaps_candidate(bbox):
            errors.append(f"visual_bbox_mismatch:{index}")
            continue
        publication_role = str(region.get("publication_role") or "body_figure")
        if publication_role != "body_figure":
            continue
        if page_class == "copyright":
            # Copyright-page graphics are publication metadata, never body
            # figures. This semantic rule applies to papers, books, and
            # monographs alike and is independent of physical page number.
            continue
        caption_bbox = region.get("caption_bbox")
        if caption_bbox is not None:
            try:
                caption_bbox = [float(value) for value in caption_bbox]
            except (TypeError, ValueError):
                caption_bbox = None
            if (caption_bbox is None or len(caption_bbox) != 4
                    or not all(0 <= value <= 1 for value in caption_bbox)
                    or caption_bbox[2] <= caption_bbox[0] or caption_bbox[3] <= caption_bbox[1]):
                errors.append(f"invalid_caption_bbox:{index}")
                caption_bbox = None
        visual_regions.append({"type": region["type"], "bbox": bbox, "caption_bbox": caption_bbox,
                               "publication_role": publication_role,
                               "confidence": region_confidence, "source": "vision_layout"})
    images = bool(raw.get("images"))
    tables = bool(raw.get("tables"))
    for semantic_type, flag in (("header", raw.get("header")), ("footer", raw.get("footer")),
                                ("footnote", raw.get("footnotes"))):
        if flag and not any(region["type"] == semantic_type for region in semantic_regions):
            mechanical_regions = [region for region in mechanical.get("candidate_semantic_regions", [])
                                  if region.get("type") == semantic_type]
            if mechanical_regions:
                semantic_regions.extend({"type": semantic_type,
                                         "bbox": [float(value) for value in region["bbox"]],
                                         "confidence": float(region.get("confidence", 0.8)),
                                         "source": "pdf_text_geometry_fallback"}
                                        for region in mechanical_regions)
            elif semantic_type != "footnote":
                errors.append(f"{semantic_type}_region_required")
    if (images or tables) and not visual_regions:
        errors.append("visual_regions_required")
    review = bool(raw.get("review_required")) or confidence < 0.75 or bool(errors)
    return {
        "schema_version": SCHEMA_VERSION, "normalizer_version": NORMALIZER_VERSION,
        "physical_page": page_no, "page_class": page_class,
        "chapter_boundary": bool(raw.get("chapter_boundary")), "header": bool(raw.get("header")),
        "footer": bool(raw.get("footer")), "columns": columns,
        "footnotes": any(region["type"] == "footnote" for region in semantic_regions),
        "note_reference_only": bool(raw.get("footnotes")) and not any(
            region["type"] == "footnote" for region in semantic_regions),
        "images": images,
        "captions": bool(raw.get("captions")), "tables": tables,
        "semantic_regions": semantic_regions,
        "visual_regions": visual_regions,
        "landscape": bool(raw.get("landscape") or mechanical["landscape"]),
        "reading_order": [str(x) for x in reading_order],
        "geometry": str(raw["geometry"]) if raw.get("geometry") is not None else None,
        "confidence": confidence, "review_required": review,
        "review_reason": raw.get("review_reason") or (", ".join(errors) if errors else ("low_confidence" if confidence < 0.75 else None)),
        "mechanical": mechanical,
    }


def _attempts(path: Path) -> list[dict[str, Any]]:
    return _jsonl(path) if path.is_file() else []


def run_structure_workspace(workspace: Path, registry: ProviderRegistry, *, provider_id: str | None = None,
                            model: str | None = None, batch_pages: int = 1,
                            max_batches: int | None = None,
                            selected_pages: list[int] | None = None,
                            attempt_ledger_path: Path | None = None,
                            attempt_context: dict[str, Any] | None = None) -> dict[str, Any]:
    workspace = workspace.resolve(); manifest = _load(workspace)
    preflight_path = workspace / "data/mechanical_preflight.jsonl"
    if not preflight_path.is_file():
        mechanical_preflight(workspace)
    mechanical = _jsonl(preflight_path)
    profile = registry.get(provider_id, "structure")
    if model:
        profile = replace(profile, model=model)
    client = registry.client(profile)
    attempts_path = workspace / "logs/structure_attempts.jsonl"
    attempts = _attempts(attempts_path)
    def current_validated(attempt: dict[str, Any]) -> bool:
        path = Path(str(attempt.get("validated_path") or ""))
        if not path.is_file():
            return False
        try:
            pages = json.loads(path.read_text("utf-8")).get("pages", [])
        except (OSError, json.JSONDecodeError):
            return False
        return bool(pages) and all(page.get("normalizer_version") == NORMALIZER_VERSION for page in pages)

    validated_by_fp = {x["fingerprint"]: x for x in attempts
                       if x.get("status") == "validated" and current_validated(x)}
    raw_by_fp = {x["fingerprint"]: x for x in attempts if x.get("raw_path") and Path(x["raw_path"]).is_file()}
    pages = sorted(set(selected_pages or range(1, len(mechanical) + 1)))
    if any(page < 1 or page > len(mechanical) for page in pages):
        raise ValueError("selected structure page is outside the source PDF")
    batches = [pages[i:i + batch_pages] for i in range(0, len(pages), batch_pages)]
    calls = 0; recovered = 0; completed = 0
    for batch_index, batch in enumerate(batches, 1):
        fingerprint = hashlib.sha256(json.dumps({"source": manifest["source_pdf_sha256"], "pages": batch,
            "provider": profile.provider_id, "model": profile.model, "prompt": PROMPT_VERSION,
            "schema": SCHEMA_VERSION}, sort_keys=True).encode()).hexdigest()
        if fingerprint in validated_by_fp:
            completed += 1; continue
        raw: dict[str, Any] | None = None
        raw_path: Path | None = None
        if fingerprint in raw_by_fp:
            raw_path = Path(raw_by_fp[fingerprint]["raw_path"])
            raw = json.loads(raw_path.read_text("utf-8")); recovered += 1
        else:
            if max_batches is not None and calls >= max_batches:
                break
            sequence = len(attempts) + 1
            attempt_id = f"structure-{sequence:05d}-{fingerprint[:12]}"
            sheet = workspace / "data/structure/contact_sheets" / f"batch-{batch_index:04d}.png"
            if len(batch) == 1:
                source_document = fitz.open(Path(manifest["source_pdf"]))
                try:
                    pixmap = source_document[batch[0] - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    sheet.parent.mkdir(parents=True, exist_ok=True)
                    pixmap.save(sheet)
                finally:
                    source_document.close()
            else:
                _contact_sheet(Path(manifest["source_pdf"]), batch, sheet)
            transition = {"attempt_id": attempt_id, "fingerprint": fingerprint, "status": "dispatching",
                          "pages": batch, "provider_id": profile.provider_id, "model": profile.model,
                          "created_at": _now(), "contact_sheet": str(sheet)}
            _append(attempts_path, transition); attempts.append(transition)
            raw = client.vision_json(
                prompt=_prompt(batch), image_path=sheet, attempt_ledger_path=attempt_ledger_path,
                attempt_context={**dict(attempt_context or {}),
                                 "page_or_segment_id": "pages:" + ",".join(str(page) for page in batch),
                                 "purpose": "publication_structure_layout_analysis"},
            )
            raw_path = workspace / "logs/structure/raw" / f"{attempt_id}.json"
            _atomic_raw(raw_path, raw)
            transition = {**transition, "status": "raw_saved", "raw_path": str(raw_path), "raw_sha256": sha256_file(raw_path),
                          "saved_at": _now(), "request_id": raw.get("id")}
            _append(attempts_path, transition); attempts.append(transition); calls += 1
        assert raw is not None and raw_path is not None
        try:
            parsed = parse_model_json(raw)
            returned = parsed.get("pages")
            if not isinstance(returned, list):
                raise ValueError("pages must be an array")
            by_page = {int(x.get("physical_page")): x for x in returned if isinstance(x, dict) and str(x.get("physical_page", "")).isdigit()}
            if set(by_page) != set(batch):
                raise ValueError("provider page set does not match request")
            records = [_normalize_record(by_page[p], p, mechanical[p - 1]) for p in batch]
            validated_path = workspace / "data/structure/validated" / f"{fingerprint}.json"
            atomic_write_json(validated_path, {"pages": records, "fingerprint": fingerprint})
            final = {"attempt_id": raw_path.stem, "fingerprint": fingerprint, "status": "validated",
                     "pages": batch, "provider_id": profile.provider_id, "model": profile.model,
                     "raw_path": str(raw_path), "validated_path": str(validated_path), "completed_at": _now()}
        except (ValueError, json.JSONDecodeError) as exc:
            final = {"attempt_id": raw_path.stem, "fingerprint": fingerprint, "status": "semantic_unresolved",
                     "pages": batch, "provider_id": profile.provider_id, "model": profile.model,
                     "raw_path": str(raw_path), "error": type(exc).__name__, "completed_at": _now()}
        _append(attempts_path, final); attempts.append(final); completed += 1
    result = finalize_structure_workspace(workspace, total_batches=len(batches), profile={"provider_id": profile.provider_id, "model": profile.model})
    result.update({"provider_calls_this_run": calls, "raw_recovered_this_run": recovered,
                   "completed_batches_this_run": completed})
    return result


def finalize_structure_workspace(workspace: Path, *, total_batches: int, profile: dict[str, str]) -> dict[str, Any]:
    workspace = workspace.resolve(); manifest = _load(workspace)
    validated_dir = workspace / "data/structure/validated"
    records: dict[int, dict[str, Any]] = {}
    paths = sorted(validated_dir.glob("*.json"), key=lambda path: (path.name != "deterministic-python.json", path.name)) if validated_dir.is_dir() else []
    for path in paths:
        for item in json.loads(path.read_text("utf-8")).get("pages", []):
            if (item.get("schema_version") != SCHEMA_VERSION
                    or item.get("normalizer_version") != NORMALIZER_VERSION):
                continue
            records[int(item["physical_page"])] = item
    attempts_path = workspace / "logs/structure_attempts.jsonl"
    attempts = _attempts(attempts_path)
    terminal_fps = {x["fingerprint"] for x in attempts if x.get("status") in {"validated", "semantic_unresolved"}}
    preflight = _jsonl(workspace / "data/mechanical_preflight.jsonl")
    for item in preflight:
        page_no = item["physical_page"]
        if page_no not in records and len(terminal_fps) >= total_batches:
            records[page_no] = _normalize_record({"page_class": "other", "confidence": 0,
                "review_required": True, "review_reason": "vlm_semantic_unresolved"}, page_no, item)
    ordered = [records[key] for key in sorted(records)]
    classification_path = workspace / "data/page_classification.jsonl"
    atomic_write_jsonl(classification_path, ordered)
    groups = []
    for item in ordered:
        if not groups or groups[-1]["page_class"] != item["page_class"] or item["chapter_boundary"]:
            groups.append({"segment_id": f"segment-{len(groups) + 1:04d}", "page_class": item["page_class"],
                           "start_page": item["physical_page"], "end_page": item["physical_page"], "pages": [item["physical_page"]]})
        else:
            groups[-1]["end_page"] = item["physical_page"]; groups[-1]["pages"].append(item["physical_page"])
    segmentation_path = workspace / "data/segmentation_plan.json"
    atomic_write_json(segmentation_path, {"schema_version": SCHEMA_VERSION, "segments": groups,
                      "deterministic_grouping": True})
    review = []
    source_document = fitz.open(Path(manifest["source_pdf"]))
    for item in ordered:
        if item["review_required"] or item["tables"] or item["images"] or item["footnotes"]:
            review.append({"object_id": f"page-{item['physical_page']:04d}-structure", "source_page": item["physical_page"],
                           "source_file_sha256": manifest["source_pdf_sha256"], "source_language": manifest["source_language"],
                           "target_language": manifest["target_language"], "issue_type": "structure_review",
                           "risk_level": "high" if item["review_required"] else "medium",
                           "ocr_text": source_document[item["physical_page"] - 1].get_text("text"),
                           "current_structure": item, "review_status": "pending",
                           "review_reason": item["review_reason"] or "complex_page_object"})
    source_document.close()
    review_path = workspace / "data/structure_review_objects.json"
    atomic_write_json(review_path, {"schema_version": "manual_review_objects.v1", "objects": review})
    publication_path = workspace / "data/publication_reconstruction.json"
    routes = []
    for item in ordered:
        complex_objects = [name for name in ("tables", "images", "captions", "footnotes") if item[name]]
        route = "manual_review" if item["review_required"] else "structured_object" if complex_objects else "reflow_text"
        routes.append({"physical_page": item["physical_page"], "page_class": item["page_class"],
                       "route": route, "objects": complex_objects,
                       "deterministic_reconstruction": route != "manual_review"})
    atomic_write_json(publication_path, {"schema_version": "publication-reconstruction-v1",
        "source_pdf_sha256": manifest["source_pdf_sha256"], "routes": routes,
        "automatic_pages": sum(x["route"] != "manual_review" for x in routes),
        "review_pages": sum(x["route"] == "manual_review" for x in routes)})
    structure_path = workspace / "data/book_structure.json"
    status = "completed" if len(ordered) == len(preflight) else "in_progress"
    atomic_write_json(structure_path, {"schema_version": SCHEMA_VERSION, "status": status,
        "source_pdf_sha256": manifest["source_pdf_sha256"], "provider": profile,
        "page_count": len(preflight), "classified_pages": len(ordered), "segments": groups,
            "review_pending": len(review), "page_class_counts": {name: sum(x["page_class"] == name for x in ordered) for name in PAGE_CLASSES}})
    manifest.update(structure_status=status, structure_provider=profile, review_pending=len(review))
    if status == "completed": manifest["stage"] = "structured"
    _save(workspace, manifest)
    return {"status": status, "classified_pages": len(ordered), "total_pages": len(preflight),
            "review_pending": len(review), "book_structure": str(structure_path),
            "page_classification": str(classification_path), "segmentation_plan": str(segmentation_path),
            "publication_reconstruction": str(publication_path), "attempt_manifest": str(attempts_path)}
