"""Versioned schemas for Phase 2B boundaries, logical content, and context."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .phase2a1 import NormalizationEvent


StructuralBreak = Literal[
    "none",
    "paragraph_break",
    "section_break",
    "chapter_break",
    "illustration_break",
    "unknown",
]
JoinOperation = Literal[
    "concatenate_without_space",
    "concatenate_with_space",
    "preserve_paragraph_break",
    "no_join",
    "uncertain",
]
HyphenType = Literal[
    "line_break_hyphen", "lexical_hyphen", "no_hyphen", "uncertain"
]


class BoundaryModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    boundary_id: str
    document_id: str
    previous_page: int = Field(ge=1)
    next_page: int = Field(ge=2)
    previous_last_block_id: str | None
    next_first_block_id: str | None
    word_continuation: bool | None
    sentence_continuation: bool | None
    paragraph_continuation: bool | None
    structural_break: StructuralBreak
    join_operation: JoinOperation
    hyphen_type: HyphenType
    header_footer_interference: bool | None
    reconstructed_boundary_text: str
    evidence: list[str]
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_triple_review: bool
    needs_human_review: bool
    status: Literal["reviewed", "needs_review"]


class BoundaryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    boundary_id: str
    document_id: str
    previous_page: int = Field(ge=1)
    next_page: int = Field(ge=2)
    next_page_available: bool = True
    previous_image_sha256: str
    next_image_sha256: str | None
    previous_last_block_id: str | None
    next_first_block_id: str | None
    previous_tail_text: str
    next_head_text: str
    word_continuation: bool | None
    sentence_continuation: bool | None
    paragraph_continuation: bool | None
    structural_break: StructuralBreak
    join_operation: JoinOperation
    hyphen_type: HyphenType
    header_footer_interference: bool | None
    reconstructed_boundary_text: str
    evidence: list[str]
    confidence: float | None = Field(default=None, ge=0, le=1)
    provider: str
    model: str | None
    review_window: list[int]
    raw_response_path: str | None
    normalization_events: list[NormalizationEvent] = Field(default_factory=list)
    model_review_status: Literal[
        "not_called", "completed", "needs_review", "schema_failed"
    ]
    human_review_status: Literal["not_required", "required", "pending", "completed"]
    needs_triple_review: bool = False
    status: Literal["reviewed", "needs_review", "open_boundary"]
    missing_required_page: int | None = None
    translation_blocked_reason: str | None = None

    @model_validator(mode="after")
    def protect_uncertain_join(self) -> "BoundaryDecision":
        uncertain = (
            self.join_operation == "uncertain"
            or self.word_continuation is None
            or self.sentence_continuation is None
            or self.paragraph_continuation is None
        )
        if uncertain and self.reconstructed_boundary_text:
            raise ValueError("Uncertain boundaries cannot contain reconstructed text")
        if self.human_review_status in {"required", "pending"} and self.reconstructed_boundary_text:
            raise ValueError("Human-review boundaries cannot auto-create reconstructed text")
        return self


CompletenessStatus = Literal[
    "complete",
    "incomplete_start",
    "incomplete_end",
    "incomplete_both",
    "unresolved_boundary",
    "needs_human_review",
]


class LogicalBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    logical_block_id: str
    document_id: str
    block_type: str
    source_pages: list[int]
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    cross_page: bool
    source_block_ids: list[str]
    source_text: str
    boundary_ids: list[str]
    word_boundary_resolved: bool
    sentence_complete: bool
    paragraph_complete: bool
    structural_context: dict[str, str | int | None]
    merge_reason: list[str]
    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertain_characters: list[str]
    unresolved_boundaries: list[str]
    model_review_status: str
    human_review_status: str
    completeness_status: CompletenessStatus
    translation_ready: bool
    created_at: datetime

    @model_validator(mode="after")
    def enforce_traceability_and_translation_gate(self) -> "LogicalBlock":
        if self.source_pages != sorted(set(self.source_pages)):
            raise ValueError("source_pages must be unique and ordered")
        if not self.source_block_ids or not self.source_text.strip():
            if self.translation_ready:
                raise ValueError("Empty or untraceable content cannot be translation ready")
        if self.page_start != min(self.source_pages) or self.page_end != max(self.source_pages):
            raise ValueError("page range must match source_pages")
        if self.cross_page != (len(self.source_pages) > 1):
            raise ValueError("cross_page must match source_pages")
        gates = [
            self.word_boundary_resolved,
            self.sentence_complete,
            self.paragraph_complete,
            not self.unresolved_boundaries,
            not self.uncertain_characters,
            self.completeness_status == "complete",
            self.human_review_status not in {"required", "pending"},
        ]
        if self.translation_ready and not all(gates):
            raise ValueError("translation_ready cannot bypass completeness gates")
        return self


class TranslationUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    translation_unit_id: str
    target_logical_block_id: str
    source_text: str
    context_before_block_ids: list[str]
    context_after_block_ids: list[str]
    context_before_text: str
    context_after_text: str
    chapter_context: str | None
    translate_target_only: Literal[True] = True
    context_complete: bool
    context_required: bool
    translation_ready: bool
    blocked_reason: str | None

    @model_validator(mode="after")
    def keep_target_and_context_separate(self) -> "TranslationUnit":
        if self.translation_ready and self.blocked_reason:
            raise ValueError("Ready translation units cannot have a blocked reason")
        if not self.source_text.strip() and self.translation_ready:
            raise ValueError("Empty source text cannot be translated")
        return self


IssueType = Literal[
    "uncertain_character",
    "uncertain_word_break",
    "uncertain_sentence_boundary",
    "uncertain_paragraph_boundary",
    "incomplete_start",
    "incomplete_end",
    "possible_omission",
    "possible_duplication",
    "header_footer_conflict",
    "chapter_boundary",
    "schema_failure",
    "model_disagreement",
    "missing_adjacent_page",
]


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    page_or_boundary: str
    source_pages: list[int]
    issue_type: IssueType
    relevant_block_ids: list[str]
    source_excerpt: str
    model_decision: str
    conflicting_evidence: str
    reason: str
    suggested_action: str
    blocking_translation: bool
    status: Literal["pending", "resolved", "not_required"] = "pending"
