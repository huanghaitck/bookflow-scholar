"""Strict Phase 3A translation request and result schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TranslatableBlockType = Literal[
    "book_title",
    "subtitle",
    "chapter_title",
    "section_title",
    "subsection_title",
    "body",
    "footnote",
    "caption",
    "table_title",
    "table_cell",
    "epigraph",
    "other_translatable",
]


class UncertainTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_term: str
    provisional_translation: str
    reason: str


class HistoricalTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_term: str
    translation: str
    note: str


class TranslationRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translation_unit_id: str
    target_block_id: str
    block_type: TranslatableBlockType
    source_text: str
    chapter_id: str | None
    section_id: str | None
    chapter_title_block_id: str | None
    section_title_block_id: str | None
    chapter_title_context_source: str | None
    chapter_title_context_translation: str | None
    section_title_context_source: str | None
    section_title_context_translation: str | None
    context_before_block_ids: list[str]
    context_after_block_ids: list[str]
    context_before_text: str | None
    context_after_text: str | None
    source_pages: list[int]
    source_language: str
    target_language: str
    translate_target_only: Literal[True] = True
    glossary: list[dict[str, Any]] = Field(default_factory=list)
    translation_profile: dict[str, Any]

    @field_validator("translation_unit_id", "target_block_id", "source_text")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class TranslationModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_block_id: str
    block_type: TranslatableBlockType
    translation: str
    uncertain_terms: list[UncertainTerm]
    historical_terms: list[HistoricalTerm]
    warnings: list[str]

    @field_validator("target_block_id", "translation")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class TranslationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_block_id: str
    block_type: TranslatableBlockType
    source_pages: list[int]
    cross_page: bool
    source_text_character_count: int
    selection_type: Literal[
        "structural_title",
        "ordinary_single_page",
        "cross_page",
        "rhetorical_long_form",
        "proper_names_or_historical_voice",
    ]
    selection_reason: str
    context_before_block_id: str | None
    context_after_block_id: str | None
    translation_ready: Literal[True]
    unresolved_boundaries: list[str]


class NormalizedTranslationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    translation_unit_id: str
    target_block_id: str
    block_type: TranslatableBlockType
    source_text_sha256: str
    context_sha256: str
    prompt_version: str
    prompt_sha256: str
    language_profile_version: str
    language_profile_sha256: str
    provider: str
    model: str
    thinking_mode: Literal["disabled"]
    target_language: str
    request_fingerprint: str
    raw_response_path: str
    translation: str
    uncertain_terms: list[UncertainTerm]
    historical_terms: list[HistoricalTerm]
    warnings: list[str]
    usage: dict[str, Any] | None
    request_id: str | None
    api_called: bool
    cache_hit: bool
    status: Literal["translated", "translation_failed"]
    created_at: datetime


class TranslationPreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    base_url: str
    api_key_env: str
    api_key_set: bool
    candidates: list[TranslationCandidate]
    target_block_count: int
    total_source_characters: int
    total_context_characters: int
    estimated_input_tokens: int
    maximum_output_tokens_per_call: int
    maximum_output_tokens_total: int
    maximum_model_list_calls: int
    maximum_content_calls: int
    input_cache_miss_price_cny_per_million_tokens: float
    output_price_cny_per_million_tokens: float
    estimated_cost_lower_cny: float
    estimated_cost_upper_cny: float
    maximum_cash_cost_cny: float
    pricing_reference_url: str
    pricing_checked_date: str
    usage_fields_expected: list[str]
    actual_charge_note: str
    blockers: list[str]
    ready_for_real_call: bool
    api_called: bool = False


class TranslationBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_block_ids: list[str]
    results: list[NormalizedTranslationResult]
    api_calls: int
    cache_hits: int
    failed: int
    retries: int = 0
    model_list_calls: int = 0
    model_available: bool | None = None
    derived_document_path: str | None = None
    diagnostic_markdown_path: str | None = None
    diagnostic_docx_path: str | None = None
    strict_export_ready: bool = False
