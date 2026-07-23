"""Versioned offline records for rendered pages and mock vision data."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "1.0"
VISION_SCHEMA_VERSION = "2.0"

BlockType = Literal[
    "chapter_title",
    "section_title",
    "body",
    "footnote",
    "caption",
    "header",
    "footer",
    "page_number",
    "illustration",
    "unknown",
]


class PageRecord(BaseModel):
    schema_version: str = SCHEMA_VERSION
    document_id: str
    source_pdf: str
    source_pdf_sha256: str
    pdf_page: int = Field(ge=1)
    pdf_page_index: int = Field(ge=0)
    printed_page: str | None = None
    page_count: int = Field(ge=1)
    image_path: str
    image_sha256: str
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    dpi: int = Field(gt=0)
    color_mode: Literal["RGB", "GRAYSCALE"]
    image_format: Literal["png"]
    text_layer_available: bool
    text_layer_character_count: int = Field(ge=0)
    render_status: Literal["completed"] = "completed"
    rendered_at: datetime
    renderer: str
    renderer_version: str
    cache_key: str
    warnings: list[str] = Field(default_factory=list)


class PageManifest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    document_id: str
    source_pdf: str
    source_pdf_sha256: str
    page_count: int = Field(ge=1)
    render_profile_id: str
    dpi: int = Field(gt=0)
    color_mode: Literal["RGB", "GRAYSCALE"]
    image_format: Literal["png"]
    renderer: str
    renderer_version: str
    selected_pages: list[int]
    page_record_paths: list[str]
    failed_pages: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class VisionBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    block_type: BlockType
    order: int = Field(ge=1)
    text: str
    bounding_box: list[float] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertain: bool = False
    notes: str | None = None


class VisionPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    document_id: str
    pdf_page: int = Field(ge=1)
    provider: str
    model: str | None = None
    source_method: str
    source_image: str
    source_image_sha256: str
    page_type: str
    printed_page: str | None = None
    title: str | None = None
    running_header: str | None = None
    footer: str | None = None
    page_number_text: str | None = None
    blocks: list[VisionBlock] = Field(default_factory=list)
    continuation_from_previous: bool | None = None
    continuation_to_next: bool | None = None
    boundary_notes: str | None = None
    uncertain_characters: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_response_path: str
    normalized_output_path: str
    input_fingerprint: str
    status: Literal[
        "mock_completed",
        "needs_real_vision",
        "technical_validation_only",
        "needs_review",
    ]
    authoritative: bool = False
    api_called: bool = False
    translation_ready: Literal[False] = False


class VisionModelPayload(BaseModel):
    """Exact JSON object requested from the visual model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    document_id: str
    pdf_page: int = Field(ge=1)
    provider: str
    model: str
    page_type: str
    printed_page: str | None
    title: str | None
    running_header: str | None
    footer: str | None
    page_number_text: str | None
    blocks: list[VisionBlock]
    continuation_from_previous: bool
    continuation_to_next: bool
    boundary_notes: str
    uncertain_characters: list[str]
    warnings: list[str]
    status: Literal["technical_validation_only", "needs_review"]
    translation_ready: Literal[False]


class ContinuityCandidate(BaseModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    document_id: str
    previous_page: int = Field(ge=1)
    next_page: int = Field(ge=1)
    previous_last_block_id: str | None = None
    next_first_block_id: str | None = None
    previous_tail_text: str = ""
    next_head_text: str = ""
    possible_word_break: bool = False
    possible_sentence_continuation: bool = False
    possible_paragraph_continuation: bool = False
    rule_signals: list[str] = Field(default_factory=list)
    model_review_required: bool = True
    human_review_required: bool = True
    decision: Literal["pending"] = "pending"
    merge_text: str = ""
    status: Literal["candidate_only"] = "candidate_only"
