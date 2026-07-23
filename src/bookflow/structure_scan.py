"""Phase 1B offline structure registration and bridge candidate discovery.

This module is purely offline: it never calls external APIs, never reads API
keys, and never instantiates a network provider.  It reuses the existing 412
page render-cache images and reference data to build a conservative physical
page registry, then derives bridge candidates that mirror the existing frozen
boundary endpoints without modifying them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import fitz  # PyMuPDF
from PIL import Image, ImageFilter

from .io_utils import (
    atomic_write_json,
    atomic_write_jsonl,
    load_json,
    sha256_file,
    stable_hash,
)
from .structure_schemas import (
    BlankKind,
    BlankPageDetail,
    BridgeCandidate,
    BridgeCandidateType,
    BridgeEligibility,
    ContentFeature,
    InterveningPageDetail,
    ManualConfirmation,
    PrimaryRole,
    RenderingPolicy,
    StructureBatchResult,
    StructurePageRecord,
    TextFlowRole,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OFFLINE_SCHEMA_VERSION = "1.0"
OFFLINE_FEATURE_PROFILE_VERSION = "v1.1"
THRESHOLD_PROFILE = "default"
EXPECTED_PDF_SHA256 = (
    "78137e1bd662e86b70cb1f197065e155fe003259c2e0244278221b4088990020"
)
EXPECTED_PAGE_COUNT = 412
DOCUMENT_ID = "doc_78137e1bd662e86b"

DEFAULT_BLANK_INK_COVERAGE_THRESHOLD = 0.01
DEFAULT_MAX_BRIDGE_DISTANCE = 10
DEFAULT_CONSECUTIVE_FAILURE_THRESHOLD = 3

MANIFEST_SUBDIR = (
    "data/fullbook/page_manifests/"
    "The_big_game_of_central_and_western_China_1913/"
    "profile_a188556af910ef78/pages"
)
AP_PAGES_SUBDIR = "data/fullbook/main_text/automated_pages/pages"
BOUNDARIES_PATH = "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl"
BACK_MATTER_PATH = "data/fullbook/back_matter/back_matter_pages_v1.json"
VISION_NORMALIZED_DIR = "data/fullbook/vision/normalized"
MANUAL_CONFIRMATIONS_PATH = (
    "data/fullbook/structure/manual/manual_confirmations.jsonl"
)

# Page-type sets for classification
_ILLUSTRATION_TYPES = frozenset({
    "illustration", "illustrated", "illustrated_page",
    "illustration_page", "image",
})
_TITLE_TYPES = frozenset({"title_page"})
_CONTENTS_TYPES = frozenset({"contents"})
_PREFACE_TYPES = frozenset({"introductory_preface", "introductory"})
_LIST_ILL_TYPES = frozenset({"list_of_illustrations", "illustration_list"})
_APPENDIX_TYPES = frozenset({"appendix_table_page", "appendix_list_page"})
_NONTEXT_TYPES = frozenset({"nontext_page", "scanning_watermark"})

# Roles that always represent content-bearing pages (even without prose).
_CONTENT_BEARING_ROLES = frozenset({
    PrimaryRole.title_page,
    PrimaryRole.contents,
    PrimaryRole.list_of_illustrations,
    PrimaryRole.appendix,
    PrimaryRole.index,
    PrimaryRole.chapter_body,
    PrimaryRole.chapter_open,
    PrimaryRole.preface,
    PrimaryRole.half_title,
    PrimaryRole.frontispiece,
    PrimaryRole.dedication,
    PrimaryRole.full_page_illustration,
    PrimaryRole.map_role,
    PrimaryRole.table_role,
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compute_cache_fingerprint(pdf_sha256: str, page_image_sha256: str) -> str:
    """Deterministic fingerprint from offline inputs only (no provider/model)."""
    return stable_hash({
        "pdf_sha256": pdf_sha256,
        "page_image_sha256": page_image_sha256,
        "offline_schema_version": OFFLINE_SCHEMA_VERSION,
        "offline_feature_profile_version": OFFLINE_FEATURE_PROFILE_VERSION,
        "threshold_profile": THRESHOLD_PROFILE,
    })


def _compute_image_stats(image_path: Path) -> tuple[float, float]:
    """Return (ink_coverage, edge_density) from a downsampled grayscale copy.

    Images wider than 100 px are downsampled to 100 px wide, preserving
    aspect ratio.  Smaller images are used at native resolution to avoid
    enlarging artifacts.
    """
    with Image.open(image_path) as img:
        if img.width > 100:
            scale = 100 / img.width
            new_w = 100
            new_h = max(1, int(img.height * scale))
        else:
            new_w = img.width
            new_h = img.height
        small = img.convert("L").resize((new_w, new_h))
        pixels = list(small.getdata())
        total = len(pixels)
        ink_count = sum(1 for p in pixels if p < 240)
        ink_coverage = ink_count / total if total else 0.0
        edges = small.filter(ImageFilter.FIND_EDGES)
        edge_pixels = list(edges.getdata())
        edge_count = sum(1 for p in edge_pixels if p > 30)
        edge_density = edge_count / total if total else 0.0
    return ink_coverage, edge_density


def _to_relative_path(absolute_path: str, root: Path) -> str:
    """Convert an absolute Windows path to a forward-slash project-relative ref."""
    p = Path(absolute_path)
    try:
        rel = p.relative_to(root.resolve())
    except ValueError:
        rel = p
    return str(rel).replace("\\", "/")


def _load_automated_page_records(root: Path) -> dict[int, dict]:
    ap_dir = root / AP_PAGES_SUBDIR
    records: dict[int, dict] = {}
    if not ap_dir.is_dir():
        return records
    for f in sorted(ap_dir.glob("page_*.json")):
        d = json.loads(f.read_text("utf-8"))
        records[d["pdf_page"]] = d
    return records


def _load_back_matter_pages(root: Path) -> dict[int, dict]:
    path = root / BACK_MATTER_PATH
    if not path.is_file():
        return {}
    data = json.loads(path.read_text("utf-8"))
    return {p["pdf_page"]: p for p in data.get("pages", [])}


def _load_vision_normalized(root: Path) -> dict[int, dict]:
    vn_dir = root / VISION_NORMALIZED_DIR
    records: dict[int, dict] = {}
    if not vn_dir.is_dir():
        return records
    for f in sorted(vn_dir.glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        pg = d.get("pdf_page")
        if pg is not None:
            records[pg] = d
    return records


def _load_boundaries(root: Path) -> list[dict]:
    path = root / BOUNDARIES_PATH
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def _load_manual_confirmations(root: Path) -> list[ManualConfirmation]:
    path = root / MANUAL_CONFIRMATIONS_PATH
    if not path.is_file():
        return []
    confirmations: list[ManualConfirmation] = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            confirmations.append(ManualConfirmation.model_validate_json(line))
    return confirmations


# ---------------------------------------------------------------------------
# Rendering-policy and blank-detail factories
# ---------------------------------------------------------------------------


def _rp_prose() -> RenderingPolicy:
    return RenderingPolicy(
        preserve_page_record=True,
        include_in_default_output=True,
        include_in_text_flow=True,
        include_in_book_element_order=True,
    )


def _rp_figure() -> RenderingPolicy:
    return RenderingPolicy(
        preserve_page_record=True,
        include_in_default_output=True,
        include_in_text_flow=False,
        include_in_book_element_order=True,
    )


def _rp_blank() -> RenderingPolicy:
    return RenderingPolicy(
        preserve_page_record=True,
        include_in_default_output=False,
        include_in_text_flow=False,
        include_in_book_element_order=False,
    )


def _rp_suppressed() -> RenderingPolicy:
    return RenderingPolicy(
        preserve_page_record=True,
        include_in_default_output=False,
        include_in_text_flow=False,
        include_in_book_element_order=False,
    )


def _rp_semantic_element() -> RenderingPolicy:
    """Rendering policy for semantic book elements (title, contents, etc.)."""
    return RenderingPolicy(
        preserve_page_record=True,
        include_in_default_output=True,
        include_in_text_flow=False,
        include_in_book_element_order=True,
    )


def _make_blank_detail(
    text_length: int,
    ink_coverage: float,
    edge_density: float,
    blank_kind: str,
    decision_source: str,
    confidence_val: float,
) -> BlankPageDetail:
    return BlankPageDetail(
        blank_kind=BlankKind(blank_kind),
        visual_blank_score=max(0.0, 1.0 - ink_coverage * 50.0),
        ocr_text_length=text_length,
        ink_coverage=ink_coverage,
        edge_density=edge_density,
        known_watermark_only=False,
        blank_confidence=confidence_val,
        blank_decision_source=decision_source,  # type: ignore[arg-type]
        blank_requires_visual_confirmation=True,
    )


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------


@dataclass
class _PageClassification:
    primary_role: PrimaryRole
    content_features: list[ContentFeature]
    contains_prose: bool
    original_book_content: bool
    bridge_eligibility: BridgeEligibility
    rendering_policy: RenderingPolicy
    classification_source: str
    evidence: list[str]
    confidence_by_field: dict[str, float]
    blank_detail: BlankPageDetail | None = None


def _classify_page(
    page: int,
    ap_record: dict | None,
    bm_record: dict | None,
    text_length: int,
    ink_coverage: float,
    edge_density: float,
    blank_ink_threshold: float,
) -> _PageClassification:
    """Conservative offline classification.

    Priority: AutomatedPageRecord (1-379) > back-matter record (380-412)
    > heuristic.  When evidence is insufficient the role is *unknown* and
    ``requires_followup`` is set downstream.
    """
    evidence: list[str] = []
    confidence: dict[str, float] = {}
    content_features: list[ContentFeature] = []
    blank_detail: BlankPageDetail | None = None

    # --- Priority 1: AutomatedPageRecord (pages 1-379) ---
    if ap_record is not None:
        page_type = ap_record.get("page_type", "")
        fragments = ap_record.get("content_fragments", [])
        block_types = [f.get("block_type", "") for f in fragments]
        has_body = "body" in block_types
        evidence.append(
            f"automated_page_record: page_type={page_type}, block_types={block_types}"
        )

        if has_body:
            evidence.append("body fragments present")
            confidence["primary_role"] = 0.95
            confidence["contains_prose"] = 0.95
            content_features.append(ContentFeature.prose)
            if "heading" in block_types:
                content_features.append(ContentFeature.heading)
            if "caption" in block_types:
                content_features.append(ContentFeature.caption)
            if "quotation" in block_types:
                content_features.append(ContentFeature.quotation)
            if "poetry" in block_types:
                content_features.append(ContentFeature.poetry)
            if ap_record.get("running_header"):
                content_features.append(ContentFeature.running_header)
            if ap_record.get("page_number_text"):
                content_features.append(ContentFeature.page_number)
            return _PageClassification(
                PrimaryRole.chapter_body, content_features, True, True,
                BridgeEligibility.not_applicable, _rp_prose(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _ILLUSTRATION_TYPES:
            content_features.append(ContentFeature.illustration)
            if "caption" in block_types:
                content_features.append(ContentFeature.caption)
            evidence.append("illustration page type")
            confidence["primary_role"] = 0.85
            confidence["contains_prose"] = 0.90
            return _PageClassification(
                PrimaryRole.full_page_illustration, content_features, False, True,
                BridgeEligibility.bridge_capable, _rp_figure(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type == "map":
            content_features.append(ContentFeature.map_feature)
            confidence["primary_role"] = 0.85
            confidence["contains_prose"] = 0.90
            return _PageClassification(
                PrimaryRole.map_role, content_features, False, True,
                BridgeEligibility.bridge_capable, _rp_figure(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type == "blank":
            blank_detail = _make_blank_detail(
                text_length, ink_coverage, edge_density,
                "unknown_blank", "cached_vision", 0.70,
            )
            confidence["primary_role"] = 0.70
            confidence["contains_prose"] = 0.90
            evidence.append("blank page type in automated record (reason unconfirmed)")
            return _PageClassification(
                PrimaryRole.blank, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_blank(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _TITLE_TYPES:
            confidence["primary_role"] = 0.80
            return _PageClassification(
                PrimaryRole.title_page, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_semantic_element(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _CONTENTS_TYPES:
            content_features.append(ContentFeature.list_feature)
            confidence["primary_role"] = 0.80
            return _PageClassification(
                PrimaryRole.contents, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_semantic_element(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _LIST_ILL_TYPES:
            content_features.append(ContentFeature.list_feature)
            confidence["primary_role"] = 0.80
            return _PageClassification(
                PrimaryRole.list_of_illustrations, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_semantic_element(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _PREFACE_TYPES:
            content_features.append(ContentFeature.prose)
            confidence["primary_role"] = 0.75
            confidence["contains_prose"] = 0.80
            return _PageClassification(
                PrimaryRole.preface, content_features, True, True,
                BridgeEligibility.not_applicable, _rp_prose(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _NONTEXT_TYPES:
            confidence["primary_role"] = 0.50
            confidence["contains_prose"] = 0.85
            evidence.append("non-text / scanning-watermark page type")
            return _PageClassification(
                PrimaryRole.unknown, content_features, False, False,
                BridgeEligibility.bridge_blocking, _rp_suppressed(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        # No fragments and potentially blank
        if not fragments:
            if text_length == 0 and ink_coverage < blank_ink_threshold:
                blank_detail = _make_blank_detail(
                    text_length, ink_coverage, edge_density,
                    "unknown_blank", "offline_heuristic", 0.60,
                )
                confidence["primary_role"] = 0.70
                confidence["contains_prose"] = 0.85
                evidence.append(
                    f"no fragments; text_len={text_length} "
                    f"ink={ink_coverage:.4f} < {blank_ink_threshold}"
                )
                return _PageClassification(
                    PrimaryRole.blank, content_features, False, True,
                    BridgeEligibility.bridge_blocking, _rp_blank(),
                    "offline_heuristic", evidence, confidence, blank_detail,
                )
            confidence["primary_role"] = 0.30
            confidence["contains_prose"] = 0.50
            evidence.append(
                f"no fragments; text_len={text_length} ink={ink_coverage:.4f}"
            )
            return _PageClassification(
                PrimaryRole.unknown, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_suppressed(),
                "offline_heuristic", evidence, confidence, blank_detail,
            )

        # Unclassified with fragments
        confidence["primary_role"] = 0.30
        confidence["contains_prose"] = 0.50
        evidence.append(f"unclassified page_type={page_type}")
        return _PageClassification(
            PrimaryRole.unknown, content_features, False, True,
            BridgeEligibility.bridge_blocking, _rp_suppressed(),
            "offline_heuristic", evidence, confidence, blank_detail,
        )

    # --- Priority 2: back-matter record (pages 380-412) ---
    if bm_record is not None:
        page_type = bm_record.get("page_type", "")
        evidence.append(f"back_matter: page_type={page_type}")

        if page_type == "blank_page":
            blank_detail = _make_blank_detail(
                text_length, ink_coverage, edge_density,
                "unknown_blank", "cached_vision", 0.70,
            )
            confidence["primary_role"] = 0.70
            confidence["contains_prose"] = 0.90
            evidence.append("blank_page in back-matter record (reason unconfirmed)")
            return _PageClassification(
                PrimaryRole.blank, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_blank(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _NONTEXT_TYPES:
            # p412 — must NOT be classified as blank
            confidence["primary_role"] = 0.50
            confidence["contains_prose"] = 0.85
            evidence.append("nontext_page: not classified as blank")
            return _PageClassification(
                PrimaryRole.unknown, content_features, False, False,
                BridgeEligibility.bridge_blocking, _rp_suppressed(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type in _APPENDIX_TYPES:
            content_features.append(ContentFeature.table_feature)
            confidence["primary_role"] = 0.80
            return _PageClassification(
                PrimaryRole.appendix, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_semantic_element(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        if page_type == "index_page":
            content_features.append(ContentFeature.index_entries)
            confidence["primary_role"] = 0.80
            return _PageClassification(
                PrimaryRole.index, content_features, False, True,
                BridgeEligibility.bridge_blocking, _rp_semantic_element(),
                "cached_vision", evidence, confidence, blank_detail,
            )

        confidence["primary_role"] = 0.40
        return _PageClassification(
            PrimaryRole.unknown, content_features, False, True,
            BridgeEligibility.bridge_blocking, _rp_suppressed(),
            "cached_vision", evidence, confidence, blank_detail,
        )

    # --- Priority 3: heuristic only (pages 384, 387, 401-404) ---
    if text_length == 0 and ink_coverage < blank_ink_threshold:
        blank_detail = _make_blank_detail(
            text_length, ink_coverage, edge_density,
            "unknown_blank", "offline_heuristic", 0.55,
        )
        confidence["primary_role"] = 0.60
        confidence["contains_prose"] = 0.80
        evidence.append(
            f"heuristic blank: text_len={text_length} ink={ink_coverage:.4f}"
        )
        return _PageClassification(
            PrimaryRole.blank, content_features, False, False,
            BridgeEligibility.bridge_blocking, _rp_blank(),
            "offline_heuristic", evidence, confidence, blank_detail,
        )

    confidence["primary_role"] = 0.30
    confidence["contains_prose"] = 0.50
    evidence.append(
        f"heuristic unknown: text_len={text_length} ink={ink_coverage:.4f}"
    )
    return _PageClassification(
        PrimaryRole.unknown, content_features, False, False,
        BridgeEligibility.bridge_blocking, _rp_suppressed(),
        "offline_heuristic", evidence, confidence, blank_detail,
    )


def _text_flow_role_for(
    primary_role: PrimaryRole,
    contains_prose: bool,
    bridge_eligibility: BridgeEligibility,
) -> TextFlowRole:
    if contains_prose:
        return TextFlowRole.prose_anchor
    if bridge_eligibility == BridgeEligibility.bridge_blocking:
        return TextFlowRole.structural_break
    if primary_role == PrimaryRole.blank:
        return TextFlowRole.non_prose_bridge
    if primary_role in (PrimaryRole.full_page_illustration, PrimaryRole.map_role):
        return TextFlowRole.non_prose_bridge
    return TextFlowRole.text_flow_none


# ---------------------------------------------------------------------------
# register_pages
# ---------------------------------------------------------------------------


def register_pages(
    *,
    root: Path,
    pdf_path: Path,
    page_numbers: list[int] | None = None,
    force: bool = False,  # noqa: ARG001 — reserved for API parity
) -> list[StructurePageRecord]:
    """Register physical pages from the authoritative PDF.

    Verifies PDF SHA-256 and page count, reuses existing render-cache images,
    and returns a :class:`StructurePageRecord` per successfully processed page.
    Pages whose manifest or render cache is missing are silently skipped
    (the caller should detect them as missing and mark them quarantined).
    """
    root = root.resolve()

    pdf_sha = sha256_file(pdf_path)
    if pdf_sha != EXPECTED_PDF_SHA256:
        raise RuntimeError(
            f"PDF SHA-256 mismatch: expected {EXPECTED_PDF_SHA256}, got {pdf_sha}"
        )

    doc = fitz.open(str(pdf_path))
    if doc.page_count != EXPECTED_PAGE_COUNT:
        raise RuntimeError(
            f"Page count mismatch: expected {EXPECTED_PAGE_COUNT}, "
            f"got {doc.page_count}"
        )

    if page_numbers is None:
        page_numbers = list(range(1, doc.page_count + 1))

    ap_records = _load_automated_page_records(root)
    bm_records = _load_back_matter_pages(root)

    manifest_dir = root / MANIFEST_SUBDIR
    records: list[StructurePageRecord] = []

    for page_num in page_numbers:
        manifest_path = manifest_dir / f"page_{page_num:04d}.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text("utf-8"))

        image_path_str = manifest.get("image_path", "")
        image_path = Path(image_path_str)
        if not image_path.is_file():
            continue

        declared_image_sha = manifest.get("image_sha256", "")
        actual_image_sha = sha256_file(image_path)
        if declared_image_sha and actual_image_sha != declared_image_sha:
            continue
        page_image_sha = actual_image_sha

        pdf_page = doc[page_num - 1]
        page_width = float(pdf_page.rect.width)
        page_height = float(pdf_page.rect.height)
        page_rotation = int(pdf_page.rotation)
        raw_text = pdf_page.get_text()
        text_length = len(raw_text.strip())
        word_count = len(raw_text.split())
        embedded_image_count = len(pdf_page.get_images())

        if manifest.get("text_layer_available"):
            text_length = int(manifest.get("text_layer_character_count", text_length))

        ink_coverage, edge_density = _compute_image_stats(image_path)

        ap_record = ap_records.get(page_num)
        bm_record = bm_records.get(page_num)

        cls = _classify_page(
            page_num, ap_record, bm_record,
            text_length, ink_coverage, edge_density,
            DEFAULT_BLANK_INK_COVERAGE_THRESHOLD,
        )

        text_flow_fragment_ids: list[str] = []
        if ap_record:
            for frag in ap_record.get("content_fragments", []):
                if frag.get("block_type") == "body":
                    text_flow_fragment_ids.append(frag["fragment_id"])

        source_ref = _to_relative_path(image_path_str, root)
        cache_fp = _compute_cache_fingerprint(pdf_sha, page_image_sha)
        content_orientation = "landscape" if page_width > page_height else "portrait"
        printed_page_label = (
            ap_record.get("printed_page") if ap_record
            else bm_record.get("printed_page") if bm_record
            else None
        )

        record = StructurePageRecord(
            document_id=DOCUMENT_ID,
            pdf_sha256=pdf_sha,
            physical_page=page_num,
            page_side="unknown",
            printed_page_label=printed_page_label,
            printed_page_number=None,
            numbering_scheme="unknown",
            page_width=page_width,
            page_height=page_height,
            page_rotation=page_rotation,
            content_orientation=content_orientation,
            primary_role=cls.primary_role,
            content_features=cls.content_features,
            artifact_overlays=[],
            text_flow_role=_text_flow_role_for(
                cls.primary_role, cls.contains_prose, cls.bridge_eligibility
            ),
            rendering_policy=cls.rendering_policy,
            content_bearing=cls.contains_prose
                or bool(cls.content_features)
                or cls.primary_role in _CONTENT_BEARING_ROLES,
            contains_prose=cls.contains_prose,
            original_book_content=cls.original_book_content,
            text_flow_fragment_ids=text_flow_fragment_ids,
            bridge_eligibility=cls.bridge_eligibility,
            requires_region_analysis=False,
            confidence_by_field=cls.confidence_by_field,
            classification_source=cls.classification_source,  # type: ignore[arg-type]
            evidence=cls.evidence,
            processing_status="offline_complete",
            cache_fingerprint=cache_fp,
            notes="",
            source_page_asset_ref=source_ref,
            page_image_sha256=page_image_sha,
            pdf_text_length=text_length,
            pdf_word_count=word_count,
            embedded_image_count=embedded_image_count,
            ink_coverage=ink_coverage,
            edge_density=edge_density,
            blank_detail=cls.blank_detail,
            requires_followup=(
                cls.primary_role == PrimaryRole.unknown
                or (
                    cls.blank_detail is not None
                    and cls.blank_detail.blank_kind == BlankKind.unknown_blank
                )
            ),
        )
        records.append(record)

    doc.close()
    return records


# ---------------------------------------------------------------------------
# discover_bridge_candidates
# ---------------------------------------------------------------------------


def _determine_bridge_type(
    details: list[InterveningPageDetail],
) -> BridgeCandidateType:
    if not details:
        return BridgeCandidateType.adjacent_prose_pages
    roles = [d.primary_role for d in details]
    has_ill = any(r == PrimaryRole.full_page_illustration for r in roles)
    has_map = any(r == PrimaryRole.map_role for r in roles)
    has_blank = any(r == PrimaryRole.blank for r in roles)
    has_unknown = any(r == PrimaryRole.unknown for r in roles)
    if has_map:
        return BridgeCandidateType.across_map
    if has_ill and has_blank:
        return BridgeCandidateType.across_illustration_and_blank_verso
    if has_ill:
        return BridgeCandidateType.across_illustration
    if has_blank:
        return BridgeCandidateType.across_blank
    return BridgeCandidateType.across_multiple_nonprose_pages


def discover_bridge_candidates(
    *,
    structure_pages: list[StructurePageRecord],
    existing_page_records: list[dict] | None = None,
    existing_boundaries: list[dict] | None = None,
    max_bridge_distance: int = DEFAULT_MAX_BRIDGE_DISTANCE,
    manual_confirmations: list[ManualConfirmation] | None = None,
) -> list[BridgeCandidate]:
    """Derive explicit bridge candidates from body-bearing pages.

    Body-bearing pages are those with non-empty ``text_flow_fragment_ids``.
    Consecutive body-page pairs mirror :func:`effective_body_page_pairs`
    without modifying the frozen boundary data.
    """
    if not 1 <= max_bridge_distance <= 50:
        raise ValueError("max_bridge_distance must be between 1 and 50")

    page_map = {p.physical_page: p for p in structure_pages}

    body_pages = sorted(
        p.physical_page for p in structure_pages
        if p.text_flow_fragment_ids and p.contains_prose
    )

    boundary_lookup: dict[tuple[int, int], dict] = {}
    if existing_boundaries:
        for b in existing_boundaries:
            boundary_lookup[(b["previous_page"], b["next_page"])] = b

    ap_lookup: dict[int, dict] = {}
    if existing_page_records:
        for pr in existing_page_records:
            pg = pr.get("pdf_page")
            if pg is not None:
                ap_lookup[int(pg)] = pr

    mc_lookup: dict[tuple[int, int], ManualConfirmation] = {}
    if manual_confirmations:
        for mc in manual_confirmations:
            if mc.candidate_id:
                parts = mc.candidate_id.split("_p")
                if len(parts) == 3:
                    try:
                        mc_lookup[(int(parts[1]), int(parts[2]))] = mc
                    except ValueError:
                        pass

    candidates: list[BridgeCandidate] = []
    skipped_pairs: list[tuple[int, int, int]] = []

    for i in range(len(body_pages) - 1):
        from_page = body_pages[i]
        to_page = body_pages[i + 1]
        gap = to_page - from_page

        if gap > max_bridge_distance:
            skipped_pairs.append((from_page, to_page, gap))
            continue

        from_rec = ap_lookup.get(from_page, {})
        to_rec = ap_lookup.get(to_page, {})
        from_frag = ""
        to_frag = ""
        if from_rec.get("tail_fragment"):
            from_frag = from_rec["tail_fragment"].get("fragment_id", "")
        if to_rec.get("head_fragment"):
            to_frag = to_rec["head_fragment"].get("fragment_id", "")

        intervening_pages = list(range(from_page + 1, to_page))
        intervening_details: list[InterveningPageDetail] = []
        for ip in intervening_pages:
            sp = page_map.get(ip)
            if sp:
                intervening_details.append(InterveningPageDetail(
                    physical_page=ip,
                    primary_role=sp.primary_role,
                    blank_kind=sp.blank_detail.blank_kind if sp.blank_detail else None,
                    content_features=list(sp.content_features),
                    evidence_source=sp.classification_source,  # type: ignore[arg-type]
                    notes=sp.notes,
                ))
            else:
                intervening_details.append(InterveningPageDetail(
                    physical_page=ip,
                    primary_role=PrimaryRole.unknown,
                    blank_kind=None,
                    content_features=[],
                    evidence_source="offline_heuristic",  # type: ignore[arg-type]
                    notes="page not registered",
                ))

        bct = _determine_bridge_type(intervening_details)
        boundary = boundary_lookup.get((from_page, to_page))
        candidate_source = "existing_boundary" if boundary else "offline_heuristic"
        mc = mc_lookup.get((from_page, to_page))

        same_chapter = True
        same_section = True
        if boundary:
            sb = boundary.get("structural_break", "none")
            if sb == "chapter_break":
                same_chapter = False
                same_section = False
            elif sb == "section_break":
                same_section = False

        requires_resolution = gap > 1 and mc is None

        if mc:
            confidence_val = 0.95
        elif boundary:
            confidence_val = 0.90
        else:
            confidence_val = 0.60

        candidates.append(BridgeCandidate(
            candidate_id=f"bridge_p{from_page:04d}_p{to_page:04d}",
            document_id=DOCUMENT_ID,
            from_page=from_page,
            from_fragment_id=from_frag,
            to_page=to_page,
            to_fragment_id=to_frag,
            intervening_pages=intervening_pages,
            intervening_page_details=intervening_details,
            same_section_candidate=same_section,
            same_chapter_candidate=same_chapter,
            bridge_candidate_type=bct,
            requires_semantic_boundary_resolution=requires_resolution,
            candidate_confidence=confidence_val,
            candidate_source=candidate_source,  # type: ignore[arg-type]
            manual_confirmation=mc,
            notes="",
        ))

    if skipped_pairs:
        raise RuntimeError(
            f"max_bridge_distance={max_bridge_distance} exceeded for "
            f"{len(skipped_pairs)} body-page pair(s); "
            f"first 5: {skipped_pairs[:5]}"
        )

    return candidates


# ---------------------------------------------------------------------------
# Checkpoint store
# ---------------------------------------------------------------------------


class StructureCheckpointStore:
    """Atomic checkpoint for offline structure registration."""

    def __init__(self, path: str | Path, *, source_pdf_sha256: str) -> None:
        self.path = Path(path)
        if self.path.is_file():
            payload = load_json(self.path)
            if payload.get("source_pdf_sha256") != source_pdf_sha256:
                raise RuntimeError(
                    "source PDF hash changed since checkpoint creation"
                )
            self._payload = payload
        else:
            now = _now()
            self._payload = {
                "schema_version": "structure-checkpoint-1.0",
                "source_pdf_sha256": source_pdf_sha256,
                "completed_pages": [],
                "quarantined_pages": {},
                "api_calls": 0,
                "created_at": now,
                "updated_at": now,
            }

    def is_completed(self, page: int) -> bool:
        return page in self._payload.get("completed_pages", [])

    @property
    def completed_pages(self) -> list[int]:
        return list(self._payload.get("completed_pages", []))

    def mark_completed(self, page: int) -> None:
        pages = self._payload.setdefault("completed_pages", [])
        if page not in pages:
            pages.append(page)
            pages.sort()
        self._payload["updated_at"] = _now()
        atomic_write_json(self.path, self._payload)

    def mark_quarantine(self, page: int, reason: str) -> None:
        self._payload.setdefault("quarantined_pages", {})[str(page)] = reason
        self._payload["updated_at"] = _now()
        atomic_write_json(self.path, self._payload)

    def clear_quarantine(self, page: int) -> None:
        """Remove a page from quarantine after successful re-registration."""
        quarantined = self._payload.get("quarantined_pages", {})
        if str(page) in quarantined:
            del quarantined[str(page)]
            self._payload["updated_at"] = _now()
            atomic_write_json(self.path, self._payload)

    @property
    def quarantined_pages(self) -> dict[str, str]:
        return dict(self._payload.get("quarantined_pages", {}))

    @property
    def api_calls(self) -> int:
        return int(self._payload.get("api_calls", 0))

    def save(self) -> None:
        self._payload["updated_at"] = _now()
        atomic_write_json(self.path, self._payload)


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


class StructureBatchRunner:
    """Minimal sequential batch runner for Phase 1B offline registration."""

    def __init__(
        self,
        *,
        root: Path,
        pdf_path: Path,
        structure_dir: str = "data/fullbook/structure",
        checkpoint_name: str = "phase1b_offline.json",
        max_bridge_distance: int = DEFAULT_MAX_BRIDGE_DISTANCE,
        consecutive_failure_threshold: int = DEFAULT_CONSECUTIVE_FAILURE_THRESHOLD,
    ) -> None:
        self.root = root.resolve()
        self.pdf_path = Path(pdf_path)
        self.structure_dir = self.root / structure_dir
        self.checkpoint_path = self.structure_dir / "checkpoints" / checkpoint_name
        self.max_bridge_distance = max_bridge_distance
        self.consecutive_failure_threshold = consecutive_failure_threshold

        pdf_sha = sha256_file(self.pdf_path)
        if pdf_sha != EXPECTED_PDF_SHA256:
            raise RuntimeError(
                f"PDF SHA-256 mismatch: expected {EXPECTED_PDF_SHA256}, "
                f"got {pdf_sha}"
            )

        self.checkpoint = StructureCheckpointStore(
            self.checkpoint_path, source_pdf_sha256=pdf_sha
        )

    # -- internal helpers --

    def _page_map_path(self) -> Path:
        return self.structure_dir / "registry" / "page_map.jsonl"

    def _bridges_path(self) -> Path:
        return self.structure_dir / "bridges" / "bridge_candidates.jsonl"

    def _quarantine_path(self) -> Path:
        return self.structure_dir / "quarantine" / "phase1b_offline.jsonl"

    def _load_all_records(self) -> list[StructurePageRecord]:
        path = self._page_map_path()
        if not path.is_file():
            return []
        records: list[StructurePageRecord] = []
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                records.append(StructurePageRecord.model_validate_json(line))
        return records

    def _is_truly_cached(
        self, page: int, records_map: dict[int, StructurePageRecord]
    ) -> bool:
        """A page is truly cached only if checkpoint, page_map and fingerprint agree."""
        if not self.checkpoint.is_completed(page):
            return False
        rec = records_map.get(page)
        if rec is None:
            return False
        expected_fp = _compute_cache_fingerprint(
            rec.pdf_sha256, rec.page_image_sha256
        )
        return rec.cache_fingerprint == expected_fp

    def _write_outputs(
        self,
        records: list[StructurePageRecord],
        candidates: list[BridgeCandidate],
        *,
        bridge_error: str | None = None,
    ) -> None:
        atomic_write_jsonl(self._page_map_path(), records)
        atomic_write_jsonl(self._bridges_path(), candidates)

        role_counts: dict[str, int] = {}
        for r in records:
            key = r.primary_role.value
            role_counts[key] = role_counts.get(key, 0) + 1

        non_adj = sum(1 for c in candidates if c.intervening_pages)

        manifest = {
            "schema_version": OFFLINE_SCHEMA_VERSION,
            "document_id": DOCUMENT_ID,
            "source_pdf_sha256": EXPECTED_PDF_SHA256,
            "total_pages": len(records),
            "page_range": [
                min(r.physical_page for r in records),
                max(r.physical_page for r in records),
            ],
            "bridge_candidate_count": len(candidates),
            "bridge_error": bridge_error,
            "non_adjacent_bridge_count": non_adj,
            "role_counts": role_counts,
            "bridge_error": bridge_error,
            "created_at": _now(),
        }
        atomic_write_json(self.structure_dir / "book_manifest.json", manifest)

    def _write_quarantine(self, quarantined: dict[str, str]) -> None:
        path = self._quarantine_path()
        entries = [
            {"physical_page": int(k), "reason": v}
            for k, v in sorted(quarantined.items(), key=lambda x: int(x[0]))
        ]
        atomic_write_jsonl(path, entries)

    # -- public API --

    def run(
        self,
        *,
        page_numbers: list[int] | None = None,
        dry_run: bool = False,
        allow_api: bool = False,
        force: bool = False,
    ) -> StructureBatchResult:
        if allow_api:
            raise NotImplementedError(
                "Phase 1B does not support API calls; allow_api must be False"
            )

        start = time.time()

        if page_numbers is None:
            page_numbers = list(range(1, EXPECTED_PAGE_COUNT + 1))

        existing_records_map = {
            r.physical_page: r for r in self._load_all_records()
        }

        if force:
            cached: list[int] = []
            pending = sorted(page_numbers)
        else:
            cached = []
            pending = []
            for p in page_numbers:
                if self._is_truly_cached(p, existing_records_map):
                    cached.append(p)
                else:
                    pending.append(p)

        if dry_run:
            return StructureBatchResult(
                total_pages=len(page_numbers),
                cached_pages=cached,
                pending_pages=pending,
                completed_pages=[],
                failed_pages=[],
                quarantined_pages=list(self.checkpoint.quarantined_pages.keys()),
                api_calls=0,
                total_tokens=0,
                consecutive_failures=0,
                stopped_by_threshold=False,
                elapsed_seconds=time.time() - start,
                checkpoint_path=str(
                    self.checkpoint_path.relative_to(self.root)
                ).replace("\\", "/"),
            )

        # Register pending pages
        new_records: list[StructurePageRecord] = []
        if pending:
            new_records = register_pages(
                root=self.root,
                pdf_path=self.pdf_path,
                page_numbers=pending,
                force=force,
            )

        registered_set = {r.physical_page for r in new_records}
        for pg in pending:
            if pg in registered_set:
                self.checkpoint.mark_completed(pg)
                self.checkpoint.clear_quarantine(pg)
            else:
                self.checkpoint.mark_quarantine(pg, "registration failed or data missing")

        completed = [r.physical_page for r in new_records]

        # Merge existing + new records
        merged: dict[int, StructurePageRecord] = dict(existing_records_map)
        for r in new_records:
            merged[r.physical_page] = r
        all_records = sorted(merged.values(), key=lambda r: r.physical_page)

        # Discover bridge candidates
        existing_boundaries = _load_boundaries(self.root)
        ap_records = list(_load_automated_page_records(self.root).values())
        manual_confs = _load_manual_confirmations(self.root)

        bridge_error: str | None = None
        candidates: list[BridgeCandidate] = []
        try:
            candidates = discover_bridge_candidates(
                structure_pages=all_records,
                existing_page_records=ap_records,
                existing_boundaries=existing_boundaries,
                max_bridge_distance=self.max_bridge_distance,
                manual_confirmations=manual_confs,
            )
        except RuntimeError as exc:
            bridge_error = str(exc)

        # Write outputs
        self._write_outputs(all_records, candidates, bridge_error=bridge_error)
        self._write_quarantine(self.checkpoint.quarantined_pages)

        # Write run summary
        role_counts: dict[str, int] = {}
        for r in all_records:
            key = r.primary_role.value
            role_counts[key] = role_counts.get(key, 0) + 1
        non_adj = sum(1 for c in candidates if c.intervening_pages)

        summary = {
            "schema_version": OFFLINE_SCHEMA_VERSION,
            "document_id": DOCUMENT_ID,
            "source_pdf_sha256": EXPECTED_PDF_SHA256,
            "total_pages": len(page_numbers),
            "cached_count": len(cached),
            "pending_count": len(pending),
            "completed_count": len(completed),
            "registered_records": len(all_records),
            "bridge_candidate_count": len(candidates),
            "bridge_error": bridge_error,
            "non_adjacent_bridge_count": non_adj,
            "role_counts": role_counts,
            "quarantined_pages": self.checkpoint.quarantined_pages,
            "api_calls": 0,
            "total_tokens": 0,
            "consecutive_failures": 0,
            "stopped_by_threshold": False,
            "elapsed_seconds": time.time() - start,
            "checkpoint_path": str(
                self.checkpoint_path.relative_to(self.root)
            ).replace("\\", "/"),
            "run_at": _now(),
        }
        atomic_write_json(self.structure_dir / "run_summary.json", summary)

        return StructureBatchResult(
            total_pages=len(page_numbers),
            cached_pages=cached,
            pending_pages=pending,
            completed_pages=completed,
            failed_pages=[],
            quarantined_pages=list(self.checkpoint.quarantined_pages.keys()),
            api_calls=0,
            total_tokens=0,
            consecutive_failures=0,
            stopped_by_threshold=False,
            elapsed_seconds=time.time() - start,
            checkpoint_path=str(
                self.checkpoint_path.relative_to(self.root)
            ).replace("\\", "/"),
        )
