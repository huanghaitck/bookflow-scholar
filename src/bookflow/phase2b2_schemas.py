"""Fail-closed schemas for automated page, boundary, logical, and export data."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AutoResolutionStatus = Literal[
    "resolved_primary",
    "resolved_pair",
    "resolved_triple",
    "resolved_text_adjudicator",
    "unresolved",
]
JoinOperationV2 = Literal[
    "insert_space",
    "remove_layout_hyphen",
    "preserve_lexical_hyphen",
    "no_join",
    "unresolved",
]


class SourceFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fragment_id: str
    text: str
    source_page: int = Field(ge=1)
    source_block_ids: list[str]
    block_type: str
    order: int = Field(ge=1)
    starts_mid_sentence: bool
    ends_mid_sentence: bool
    starts_mid_paragraph: bool
    ends_mid_paragraph: bool
    visible_trailing_hyphen: bool
    uncertainty: list[str] = Field(default_factory=list)


class AutomatedPageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    document_id: str
    pdf_page: int = Field(ge=1)
    printed_page: str | None
    page_type: str
    full_visible_text: str
    complete_blocks: list[str]
    head_fragment: SourceFragment | None
    tail_fragment: SourceFragment | None
    content_fragments: list[SourceFragment]
    running_header: str | None
    footer: str | None
    page_number_text: str | None
    titles: list[str]
    image_sha256: str
    text_layer_text: str
    text_layer_similarity: float = Field(ge=0, le=1)
    transcription_status: str
    source_coverage_status: Literal["complete", "partial", "failed"]
    legacy_normalized_path: str
    created_at: datetime


class AutomatedBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    boundary_id: str
    document_id: str
    previous_page: int = Field(ge=1)
    next_page: int = Field(ge=2)
    next_page_available: bool = True
    previous_fragment_id: str
    next_fragment_id: str | None
    previous_tail_text: str
    next_head_text: str
    word_continuation: bool | None
    sentence_continuation: bool | None
    paragraph_continuation: bool | None
    structural_break: Literal[
        "none", "paragraph_break", "section_break", "chapter_break", "illustration_break", "unknown"
    ]
    join_operation: JoinOperationV2
    visible_trailing_hyphen: bool
    resolution_method: str
    supporting_evidence: list[str]
    conflicting_evidence: list[str]
    resolution_reason: str
    auto_resolution_status: AutoResolutionStatus
    source_inputs: list[str]
    text_adjudicator_called: bool = False
    translation_called: bool = False
    created_at: datetime

    @model_validator(mode="after")
    def fail_closed(self) -> "AutomatedBoundary":
        if self.auto_resolution_status == "unresolved" and self.join_operation != "unresolved":
            raise ValueError("unresolved boundaries must not contain a join decision")
        if self.structural_break != "none" and self.join_operation not in {"no_join", "unresolved"}:
            raise ValueError("structural breaks cannot join content")
        if self.join_operation == "remove_layout_hyphen" and not self.visible_trailing_hyphen:
            raise ValueError("a layout hyphen cannot be removed unless it is visibly present")
        return self


class AutomatedLogicalBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    logical_block_id: str
    document_id: str
    source_pages: list[int]
    source_fragment_ids: list[str]
    source_block_ids: list[str]
    source_text: str
    block_type: str
    chapter_id: str | None
    section_id: str | None
    cross_page: bool
    sentence_complete: bool
    paragraph_complete: bool
    coverage_complete: bool
    unresolved_boundaries: list[str]
    header_footer_page_number_clean: bool
    translation_ready: bool
    created_at: datetime

    @model_validator(mode="after")
    def enforce_translation_gate(self) -> "AutomatedLogicalBlock":
        gates = [
            self.sentence_complete,
            self.paragraph_complete,
            self.coverage_complete,
            not self.unresolved_boundaries,
            bool(self.source_text.strip()),
            self.header_footer_page_number_clean,
            len(self.source_fragment_ids) == len(set(self.source_fragment_ids)),
        ]
        if self.translation_ready and not all(gates):
            raise ValueError("translation_ready cannot bypass automated completeness gates")
        return self


class TranslationContextV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_logical_block_id: str
    source_text: str
    context_before_text: str
    context_after_text: str
    chapter_context: str | None
    translate_target_only: Literal[True] = True
    context_complete: bool
    translation_ready: bool


class CarryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["carry_in", "carry_out"]
    batch_start: int
    batch_end: int
    fragment_id: str
    source_page: int
    text: str
    completeness_status: Literal["complete", "incomplete_start", "incomplete_end", "unresolved"]
    expected_next_page: int | None


class SourceCoverageAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_pages: int
    processed_pages: int
    missing_pages: list[int]
    duplicate_pages: list[int]
    discontinuous_pages: list[int]
    page_hashes_valid: bool
    transcription_missing_pages: list[int]
    partial_coverage_pages: list[int]
    all_visible_source_recorded: bool
    passed: bool


class LogicalReconstructionAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fragment_count: int
    referenced_fragment_count: int
    unreferenced_fragment_ids: list[str]
    duplicate_fragment_ids: list[str]
    unresolved_boundary_ids: list[str]
    internal_unresolved_boundary_ids: list[str]
    external_open_boundary_ids: list[str]
    internal_boundaries_passed: bool
    unsupported_no_space_boundaries: list[str]
    untraceable_logical_block_ids: list[str]
    chapter_break_join_violations: list[str]
    passed: bool


class TranslationAlignmentAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_run", "passed", "failed"]
    ready_source_blocks: int
    translated_blocks: int
    missing_translation_ids: list[str]
    duplicate_translation_ids: list[str]
    context_leak_ids: list[str]
    incomplete_translated_ids: list[str]
    passed: bool


class BilingualEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_block_id: str
    source_pages: list[int]
    source_text: str
    chinese_text: str | None = None
    translation_status: Literal["not_translated", "translated"] = "not_translated"
    translation_ready: bool
    unresolved_boundaries: list[str]


class BilingualDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    document_id: str
    source_pdf: str
    source_pdf_sha256: str
    entries: list[BilingualEntry]
    source_coverage_audit: SourceCoverageAudit
    logical_reconstruction_audit: LogicalReconstructionAudit
    translation_alignment_audit: TranslationAlignmentAudit
    carry_records: list[CarryRecord]
    strict_export_ready: bool
    strict_blockers: list[str]
    api_calls: int = 0
    deepseek_calls: int = 0
    translation_calls: int = 0
    created_at: datetime
