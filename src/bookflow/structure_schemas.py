"""Schemas for Phase 1B offline structure registration and bridge candidate discovery."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PrimaryRole(str, Enum):
    cover = "cover"
    blank = "blank"
    half_title = "half_title"
    frontispiece = "frontispiece"
    title_page = "title_page"
    digitization_notice = "digitization_notice"
    dedication = "dedication"
    preface = "preface"
    contents = "contents"
    list_of_illustrations = "list_of_illustrations"
    map_role = "map"
    chapter_open = "chapter_open"
    chapter_body = "chapter_body"
    full_page_illustration = "full_page_illustration"
    appendix = "appendix"
    table_role = "table"
    index = "index"
    library_artifact = "library_artifact"
    back_cover = "back_cover"
    unknown = "unknown"


class ContentFeature(str, Enum):
    prose = "prose"
    heading = "heading"
    caption = "caption"
    quotation = "quotation"
    poetry = "poetry"
    list_feature = "list"
    illustration = "illustration"
    map_feature = "map"
    table_feature = "table"
    index_entries = "index_entries"
    page_number = "page_number"
    running_header = "running_header"
    footnote = "footnote"
    marginalia = "marginalia"
    watermark = "watermark"
    library_stamp = "library_stamp"


class ArtifactOverlay(str, Enum):
    digitization_watermark = "digitization_watermark"
    library_stamp = "library_stamp"
    barcode = "barcode"
    binding_shadow = "binding_shadow"
    scan_artifact = "scan_artifact"
    overexposure = "overexposure"
    underexposure = "underexposure"


class TextFlowRole(str, Enum):
    prose_anchor = "prose_anchor"
    prose_continuation = "prose_continuation"
    non_prose_bridge = "non_prose_bridge"
    structural_break = "structural_break"
    text_flow_none = "text_flow_none"


class BridgeEligibility(str, Enum):
    bridge_capable = "bridge_capable"
    bridge_blocking = "bridge_blocking"
    not_applicable = "not_applicable"


class BlankKind(str, Enum):
    intentional_blank = "intentional_blank"
    plate_verso_blank = "plate_verso_blank"
    scan_blank = "scan_blank"
    watermark_only_blank = "watermark_only_blank"
    unknown_blank = "unknown_blank"


class BridgeCandidateType(str, Enum):
    adjacent_prose_pages = "adjacent_prose_pages"
    across_blank = "across_blank"
    across_illustration = "across_illustration"
    across_illustration_and_blank_verso = "across_illustration_and_blank_verso"
    across_map = "across_map"
    across_multiple_nonprose_pages = "across_multiple_nonprose_pages"


class RenderingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preserve_page_record: bool
    include_in_default_output: bool
    include_in_text_flow: bool
    include_in_book_element_order: bool
    include_in_faithful_edition_future: bool = False
    notes: str = ""


class BlankPageDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blank_kind: BlankKind
    visual_blank_score: float = Field(ge=0, le=1)
    ocr_text_length: int = Field(ge=0)
    ink_coverage: float = Field(ge=0, le=1)
    edge_density: float = Field(ge=0, le=1)
    known_watermark_only: bool
    blank_confidence: float = Field(ge=0, le=1)
    blank_decision_source: Literal[
        "offline_heuristic", "cached_vision", "vision_api", "inferred", "manual"
    ]
    blank_requires_visual_confirmation: bool


class InterveningPageDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    physical_page: int = Field(ge=1)
    primary_role: PrimaryRole
    blank_kind: BlankKind | None = None
    content_features: list[ContentFeature]
    evidence_source: Literal[
        "offline_heuristic", "cached_vision", "vision_api", "manual", "inferred"
    ]
    notes: str = ""


class ManualConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_by: str
    confirmed_at: str
    confirmation_scope: Literal[
        "continuous_prose", "same_section", "same_chapter", "intervening_page_roles"
    ]
    summary: str
    evidence_references: list[str] = Field(default_factory=list)
    candidate_id: str | None = None


class StructurePageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    pdf_sha256: str
    physical_page: int = Field(ge=1)
    page_side: Literal["recto", "verso", "unknown"]
    printed_page_label: str | None
    printed_page_number: int | None
    numbering_scheme: Literal[
        "none", "roman_lowercase", "roman_uppercase",
        "arabic", "mixed", "unknown"
    ]
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    page_rotation: int = Field(ge=0, le=359)
    content_orientation: Literal["portrait", "landscape", "unknown"]
    primary_role: PrimaryRole
    content_features: list[ContentFeature]
    artifact_overlays: list[ArtifactOverlay]
    text_flow_role: TextFlowRole
    rendering_policy: RenderingPolicy
    content_bearing: bool
    contains_prose: bool
    original_book_content: bool
    text_flow_fragment_ids: list[str]
    bridge_eligibility: BridgeEligibility
    requires_region_analysis: bool
    confidence_by_field: dict[str, float]
    classification_source: Literal[
        "offline_heuristic", "cached_vision", "vision_api", "manual", "inferred"
    ]
    evidence: list[str]
    processing_status: Literal[
        "pending", "offline_complete", "vision_complete",
        "quarantined", "failed", "deferred"
    ]
    cache_fingerprint: str
    notes: str = ""
    source_page_asset_ref: str
    page_image_sha256: str
    pdf_text_length: int = Field(ge=0)
    pdf_word_count: int = Field(ge=0)
    embedded_image_count: int = Field(ge=0)
    ink_coverage: float = Field(ge=0, le=1)
    edge_density: float = Field(ge=0, le=1)
    blank_detail: BlankPageDetail | None = None
    requires_followup: bool = False

    @field_validator("source_page_asset_ref")
    @classmethod
    def validate_asset_ref_relative(cls, value: str) -> str:
        """Reject absolute paths with drive letters or leading slashes."""
        if not value:
            raise ValueError("source_page_asset_ref must not be empty")
        if len(value) >= 2 and value[1] == ":":
            raise ValueError(
                "source_page_asset_ref must be a project-relative path, "
                "not an absolute path with a drive letter"
            )
        if value.startswith("/") or value.startswith("\\"):
            raise ValueError(
                "source_page_asset_ref must be a project-relative path, "
                "not an absolute path"
            )
        return value


class BridgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    document_id: str
    from_page: int = Field(ge=1)
    from_fragment_id: str
    to_page: int = Field(ge=2)
    to_fragment_id: str
    intervening_pages: list[int]
    intervening_page_details: list[InterveningPageDetail]
    same_section_candidate: bool
    same_chapter_candidate: bool
    bridge_candidate_type: BridgeCandidateType
    requires_semantic_boundary_resolution: bool
    candidate_confidence: float = Field(ge=0, le=1)
    candidate_source: Literal[
        "offline_heuristic", "cached_vision", "inferred", "manual", "existing_boundary"
    ]
    manual_confirmation: ManualConfirmation | None = None
    notes: str = ""

    @model_validator(mode="after")
    def check_intervening_consistency(self) -> "BridgeCandidate":
        if len(self.intervening_pages) != len(self.intervening_page_details):
            raise ValueError("intervening_pages and intervening_page_details must have equal length")
        return self

    @model_validator(mode="after")
    def check_page_order(self) -> "BridgeCandidate":
        if self.from_page >= self.to_page:
            raise ValueError("from_page must be less than to_page")
        return self


class StructureCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    source_pdf_sha256: str
    completed_pages: list[int]
    quarantined_pages: dict[str, str]
    api_calls: int = 0
    created_at: datetime
    updated_at: datetime


class StructureBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_pages: int
    cached_pages: list[int]
    pending_pages: list[int]
    completed_pages: list[int]
    failed_pages: list[int]
    quarantined_pages: list[int]
    api_calls: int
    total_tokens: int
    consecutive_failures: int
    stopped_by_threshold: bool
    elapsed_seconds: float
    checkpoint_path: str