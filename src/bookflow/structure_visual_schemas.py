"""Schemas for Phase 1C-B visual page structure classification.

These models define the contract between the offline request builder, the
mocked provider adapter, and the future real visual API calls.  They reuse
enums from ``structure_schemas`` and enforce strict validation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .structure_schemas import (
    ArtifactOverlay,
    BlankKind,
    ContentFeature,
    PrimaryRole,
)

# ---------------------------------------------------------------------------
# Enums specific to visual classification
# ---------------------------------------------------------------------------


class NumberingScheme(str, Enum):
    none = "none"
    roman_lowercase = "roman_lowercase"
    roman_uppercase = "roman_uppercase"
    arabic = "arabic"
    mixed = "mixed"
    unknown = "unknown"


class PageSide(str, Enum):
    recto = "recto"
    verso = "verso"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Field evidence and warnings
# ---------------------------------------------------------------------------


class FieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    observed: str
    basis: Literal["visual", "text_layer", "image_stats", "structural_context", "heuristic"]
    confidence: float = Field(ge=0, le=1)


class VisualClassificationWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class VisualRequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    physical_page: int = Field(ge=1)
    source_page_asset_ref: str
    page_image_sha256: str
    pdf_text_length: int = Field(ge=0)
    pdf_word_count: int = Field(ge=0)
    embedded_image_count: int = Field(ge=0)
    ink_coverage: float = Field(ge=0, le=1)
    edge_density: float = Field(ge=0, le=1)
    current_primary_role: str
    current_content_features: list[str]
    current_classification_source: str
    current_evidence_summary: list[str]
    neighboring_prose_pages: list[int]
    neighboring_primary_roles: list[str]
    target_group: str
    requested_visual_questions: list[str]


class VisualPageClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    schema_version: str = "1.0"
    prompt_version: str = "v1"
    physical_page: int = Field(ge=1)
    context: VisualRequestContext
    request_fingerprint: str


class VisualBatchRequestManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    schema_version: str = "1.0"
    prompt_version: str = "v1"
    page_count: int = Field(ge=1)
    physical_pages: list[int]
    api_calls_allowed: bool = False
    created_at: str


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class VisualPageClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    physical_page: int = Field(ge=1)
    primary_role: PrimaryRole
    blank_kind: BlankKind | None = None
    content_features: list[ContentFeature]
    artifact_overlays: list[ArtifactOverlay] = Field(default_factory=list)
    original_book_content: bool
    contains_prose: bool
    safe_to_exclude_from_prose_flow: bool
    requires_region_analysis: bool
    printed_page_label: str | None = None
    printed_page_number: int | None = None
    numbering_scheme: NumberingScheme = NumberingScheme.unknown
    page_side: PageSide = PageSide.unknown
    field_evidence: list[FieldEvidence]
    confidence_by_field: dict[str, float] = Field(default_factory=dict)
    warnings: list[VisualClassificationWarning] = Field(default_factory=list)
    reviewer_notes: str = ""
    raw_response_ref: str | None = None

    @model_validator(mode="after")
    def check_blank_consistency(self) -> "VisualPageClassificationResponse":
        if self.primary_role == PrimaryRole.blank:
            if self.blank_kind is None:
                raise ValueError(
                    "blank_kind must be non-null when primary_role is blank"
                )
        else:
            if self.blank_kind is not None:
                raise ValueError(
                    "blank_kind must be null when primary_role is not blank"
                )
        # Validate all confidence values are in [0, 1]
        for field_name, val in self.confidence_by_field.items():
            if not isinstance(val, (int, float)) or val < 0 or val > 1:
                raise ValueError(
                    f"confidence_by_field['{field_name}']={val} is out of range [0, 1]"
                )
        return self


# ---------------------------------------------------------------------------
# Provider request/result models
# ---------------------------------------------------------------------------


class VisualProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    physical_page: int = Field(ge=1)
    source_page_asset_ref: str
    prompt: str
    context_json: str
    request_fingerprint: str
    schema_version: str = "1.0"


class VisualProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    physical_page: int = Field(ge=1)
    response: VisualPageClassificationResponse
    raw_content: str
    raw_response_ref: str | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Provider protocol (structural typing, no runtime enforcement)
# ---------------------------------------------------------------------------


class StructureVisualProvider(Protocol):
    """Protocol for structure visual classification providers."""

    def classify_page(
        self,
        request: VisualProviderRequest,
        *,
        allow_network: bool = False,
    ) -> VisualProviderResult:
        ...


# ---------------------------------------------------------------------------
# Sentinel: forbidden field names in responses
# ---------------------------------------------------------------------------

FORBIDDEN_RESPONSE_FIELDS = frozenset({
    "join_operation",
    "structural_break",
    "from_page",
    "to_page",
    "from_fragment_id",
    "to_fragment_id",
    "intervening_pages",
})
