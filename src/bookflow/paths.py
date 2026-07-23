"""Project paths and configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectSettings(BaseModel):
    """Non-secret project settings loaded from YAML."""

    source_pdf: str
    sample_pdf: str
    output_directory: str
    cache_directory: str
    render_dpi: int = Field(default=200, ge=72, le=600)
    render_format: str = "png"
    render_color_mode: str = "RGB"
    page_image_directory: str = "data/pages"
    manifest_directory: str = "data/pages_manifests"
    mock_vision_directory: str = "data/mock_vision"
    continuity_directory: str = "data/continuity"
    log_directory: str = "data/logs"
    temporary_directory: str = "data/tmp"
    vision_window_size: int = Field(default=1, ge=1)
    vision_overlap_pages: int = Field(default=0, ge=0)
    resume: bool = True
    force: bool = False
    sample_page_range: list[int] = Field(default_factory=lambda: [1, 11])
    vision_provider: str
    vision_base_url: str
    vision_model: str
    vision_api_key_env: str = "ZAI_API_KEY"
    vision_compatible_api_key_envs: list[str] = Field(default_factory=list)
    vision_input_mode: str = "base64_data_url"
    vision_max_output_tokens: int = Field(default=8000, ge=1, le=8000)
    vision_temperature: float = Field(default=0, ge=0, le=2)
    vision_do_sample: bool = False
    vision_thinking_mode: str = "disabled"
    vision_api_enabled: bool = False
    vision_maximum_real_calls: int = Field(default=1, ge=0, le=1)
    vision_automatic_retry: bool = False
    vision_response_format_json_object: bool = False
    vision_request_timeout_seconds: float = Field(default=180, gt=0, le=600)
    vision_maximum_cash_cost_cny: float = Field(default=0.50, ge=0, le=0.50)
    vision_context_window_tokens: int = Field(default=128000, ge=1)
    vision_input_price_cny_per_million_tokens: float = Field(default=1.0, ge=0)
    vision_output_price_cny_per_million_tokens: float = Field(default=3.0, ge=0)
    vision_input_price_cny_per_million_tokens_upper: float = Field(default=2.0, ge=0)
    vision_output_price_cny_per_million_tokens_upper: float = Field(default=6.0, ge=0)
    vision_pricing_reference_url: str = "https://bigmodel.cn/pricing"
    vision_pricing_checked_date: str = "2026-07-15"
    vision_prompt_path: str = "prompts/vision_transcription_v1.md"
    vision_prompt_version: str = "vision_transcription_v1"
    vision_raw_directory: str = "data/vision_raw"
    vision_normalized_directory: str = "data/vision_normalized"
    vision_request_directory: str = "data/vision_requests"
    vision_usage_directory: str = "data/vision_usage"
    vision_cache_directory: str = "data/vision_cache"
    full_pdf_protection: bool = True
    translation_enabled: bool = False
    translation_disabled: bool = True
    terminology_translation_disabled: bool = True
    automatic_phase_advance: bool = False
    phase3b_source_sample_pdf: str = "input/sample_12_pages.pdf"
    phase3b_page12_pdf: str = "input/sample_page_12.pdf"
    phase3b_max_single_calls: int = Field(default=1, ge=0, le=1)
    phase3b_max_pair_calls: int = Field(default=1, ge=0, le=1)
    phase3b_max_triple_calls: int = Field(default=0, ge=0, le=0)
    phase3b_max_total_calls: int = Field(default=2, ge=0, le=2)
    phase3b_automatic_retry: bool = False
    phase3b_maximum_cash_cost_cny: float = Field(default=0.50, ge=0, le=0.50)
    phase3b_data_directory: str = "data/phase3b_source_sample12"
    phase3b_master_path: str = "data/source_document_sample12_v1.json"
    phase3b_diagnostic_directory: str = "output/diagnostic"
    phase3b_final_directory: str = "output/final"
    vision_normalized_schema_version: str = "1.1"
    vision_normalized_v11_directory: str = "data/vision_normalized_v1_1"
    phase2b_page_prompt_path: str = "prompts/vision_transcription_v2.md"
    phase2b_page_prompt_version: str = "vision_transcription_v2"
    boundary_prompt_path: str = "prompts/boundary_review_v1.md"
    boundary_prompt_version: str = "boundary_review_v1"
    boundary_schema_version: str = "1.0"
    logical_schema_version: str = "1.0"
    translation_context_schema_version: str = "1.0"
    boundary_raw_directory: str = "data/boundary_raw"
    boundary_normalized_directory: str = "data/boundary_normalized"
    boundary_cache_directory: str = "data/boundary_cache"
    boundary_review_directory: str = "data/boundary_reviews"
    logical_block_directory: str = "data/logical_blocks"
    logical_manifest_directory: str = "data/logical_manifests"
    translation_context_directory: str = "data/translation_context"
    human_review_directory: str = "data/review"
    boundary_qa_reference_path: str = "config/sample_boundary_qa_reference.yaml"
    boundary_qa_output_path: str = "data/review/sample_boundary_qa.json"
    phase2b_request_directory: str = "data/phase2b_requests"
    phase2b_usage_directory: str = "data/phase2b_usage"
    phase2b_page_cache_directory: str = "data/phase2b_page_cache"
    phase2b_max_single_calls: int = Field(default=10, ge=0, le=10)
    phase2b_max_pair_calls: int = Field(default=10, ge=0, le=10)
    phase2b_max_triple_calls: int = Field(default=3, ge=0, le=3)
    phase2b_max_total_calls: int = Field(default=23, ge=0, le=23)
    phase2b_maximum_estimated_cash_cost_cny: float = Field(default=0.50, ge=0)
    phase2b_automatic_retry: bool = False
    phase2b_translation_enabled: bool = False
    automated_page_schema_version: str = "2.0"
    automated_boundary_schema_version: str = "2.0"
    automated_logical_schema_version: str = "2.0"
    automated_page_directory: str = "data/automated_pages"
    automated_boundary_directory: str = "data/automated_boundaries"
    automated_logical_directory: str = "data/automated_logical_blocks"
    automated_context_directory: str = "data/automated_translation_context"
    automated_audit_directory: str = "data/automated_audits"
    automated_master_path: str = "data/bilingual_document.json"
    automated_export_directory: str = "output/diagnostic"
    automated_batch_size: int = Field(default=10, ge=2, le=20)
    automated_batch_overlap: int = Field(default=1, ge=1, le=1)
    automated_text_layer_coverage_threshold: float = Field(default=0.80, ge=0, le=1)
    automated_vision_prompt_path: str = "prompts/vision_transcription_v3.md"
    automated_boundary_prompt_path: str = "prompts/boundary_observation_v2.md"
    text_boundary_adjudicator_prompt_path: str = "prompts/text_boundary_adjudicator_v1.md"
    translation_provider: str
    translation_base_url: str
    translation_model: str
    translation_api_key_env: str = "DEEPSEEK_API_KEY"
    translation_source_language: str = "en"
    translation_target_language: str = "zh-Hans"
    translation_thinking_mode: str = "disabled"
    translation_response_format_json_object: bool = True
    translation_temperature: float = Field(default=0, ge=0, le=2)
    translation_max_output_tokens: int = Field(default=4096, ge=1, le=8192)
    translation_request_timeout_seconds: float = Field(default=180, gt=0, le=600)
    translation_automatic_retry: bool = False
    translation_maximum_real_calls: int = Field(default=5, ge=0, le=5)
    translation_maximum_model_list_calls: int = Field(default=1, ge=0, le=1)
    translation_maximum_cash_cost_cny: float = Field(default=1.0, ge=0, le=1.0)
    translation_input_cache_hit_price_cny_per_million_tokens: float = Field(default=0.025, ge=0)
    translation_input_cache_miss_price_cny_per_million_tokens: float = Field(default=3.0, ge=0)
    translation_output_price_cny_per_million_tokens: float = Field(default=6.0, ge=0)
    translation_pricing_reference_url: str = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
    translation_pricing_checked_date: str = "2026-07-15"
    translation_prompt_path: str = "prompts/translation_en_zh_v1.md"
    translation_prompt_version: str = "translation_en_zh_v1"
    translation_language_profile_path: str = "language_profiles/zh-Hans.yaml"
    translation_language_profile_version: str = "zh-Hans-v1"
    translation_schema_version: str = "1.1"
    translation_request_directory: str = "data/translation_requests"
    translation_raw_directory: str = "data/translation_raw"
    translation_normalized_directory: str = "data/translation_normalized"
    translation_cache_directory: str = "data/translation_cache"
    translation_usage_directory: str = "data/translation_usage"
    translation_report_directory: str = "data/translation_reports"
    translation_derived_document_path: str = "data/phase3a_translation_sample.json"
    translation_diagnostic_markdown_path: str = "output/diagnostic/phase3a_translation_sample.md"
    translation_diagnostic_docx_path: str = "output/diagnostic/phase3a_translation_sample.docx"
    translation_api_enabled: bool = False
    phase3c4_source_document_path: str = "data/source_document_sample12_v1.json"
    phase3c4_master_path: str = "data/bilingual_document_sample12_zh-Hans_v1.json"
    phase3c4_data_directory: str = "data/phase3c4_translation"
    phase3c4_candidate_directory: str = "output/candidate"
    phase3c4_rendered_directory: str = "output/rendered"
    phase3c4_libreoffice_profile_directory: str = "data/phase3c4_libreoffice_profile"
    phase3c4_soffice_candidates: list[str] = Field(
        default_factory=lambda: [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    )
    phase3c4_prompt_path: str = "prompts/translation_en_zh_v2.md"
    phase3c4_prompt_version: str = "translation_en_zh_v2.0"
    phase3c4_schema_version: str = "1.0"
    phase3c4_maximum_content_calls: int = Field(default=24, ge=0, le=24)
    phase3c4_maximum_model_list_calls: int = Field(default=1, ge=0, le=1)
    phase3c4_maximum_cash_cost_cny: float = Field(default=2.0, ge=0, le=2.0)
    phase3c4_automatic_retry: bool = False
    phase3c4_expected_block_count: int = Field(default=24, ge=24, le=24)
    phase3c4_pricing_checked_date: str = "2026-07-16"
    processing_page_batch_size: int = Field(default=20, ge=1, le=100)
    translation_checkpoint_interval: int = Field(default=50, ge=1, le=1000)
    fullbook_data_directory: str = "data/fullbook"
    fullbook_candidate_directory: str = "output/fullbook/candidate"
    fullbook_rendered_directory: str = "output/fullbook/rendered"
    fullbook_final_directory: str = "output/fullbook/final"
    fullbook_checkpoint_path: str = "data/fullbook/checkpoints/production.json"
    fullbook_source_document_path: str = "data/fullbook/source_document_full_v1.json"
    fullbook_bilingual_document_path: str = "data/fullbook/bilingual_document_full_zh-Hans_v1.json"
    structure_data_directory: str = "data/fullbook/structure"
    structure_max_bridge_distance: int = Field(default=10, ge=1, le=50)
    structure_blank_ink_coverage_threshold: float = Field(default=0.01, ge=0, le=1)
    structure_consecutive_failure_threshold: int = Field(default=3, ge=1, le=10)
    maximum_cash_cost_cny: float = Field(ge=0)
    default_page_range: list[int]
    dry_run: bool = True

    @field_validator("default_page_range")
    @classmethod
    def validate_page_range(cls, value: list[int]) -> list[int]:
        if len(value) != 2 or value[0] < 1 or value[1] < value[0]:
            raise ValueError("default_page_range must be [start, end] with 1 <= start <= end")
        return value

    @field_validator("sample_page_range")
    @classmethod
    def validate_sample_page_range(cls, value: list[int]) -> list[int]:
        if len(value) != 2 or value[0] < 1 or value[1] < value[0]:
            raise ValueError("sample_page_range must be [start, end] with 1 <= start <= end")
        return value

    @field_validator("render_format")
    @classmethod
    def validate_render_format(cls, value: str) -> str:
        normalized = value.lower().lstrip(".")
        if normalized != "png":
            raise ValueError("Phase 1B supports PNG rendering only")
        return normalized

    @field_validator("render_color_mode")
    @classmethod
    def validate_render_color_mode(cls, value: str) -> str:
        normalized = value.strip().upper()
        aliases = {"GRAY": "GRAYSCALE", "GREY": "GRAYSCALE", "L": "GRAYSCALE"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"RGB", "GRAYSCALE"}:
            raise ValueError("render_color_mode must be RGB or grayscale")
        return normalized

    @model_validator(mode="after")
    def validate_vision_window(self) -> "ProjectSettings":
        if self.vision_overlap_pages >= self.vision_window_size:
            raise ValueError("vision_overlap_pages must be smaller than vision_window_size")
        if (
            self.phase2b_max_single_calls
            + self.phase2b_max_pair_calls
            + self.phase2b_max_triple_calls
            != self.phase2b_max_total_calls
        ):
            raise ValueError("Phase 2B category call limits must equal the total limit")
        if self.automated_batch_overlap >= self.automated_batch_size:
            raise ValueError("automated_batch_overlap must be smaller than automated_batch_size")
        if self.translation_thinking_mode != "disabled":
            raise ValueError("Phase 3A requires translation_thinking_mode=disabled")
        if not self.translation_response_format_json_object:
            raise ValueError("Phase 3A requires JSON Output")
        if self.translation_automatic_retry:
            raise ValueError("Phase 3A automatic retry must remain disabled")
        if self.translation_maximum_real_calls != 5:
            raise ValueError("Phase 3A content call limit must equal five")
        if self.translation_maximum_model_list_calls != 1:
            raise ValueError("Phase 3A model-list call limit must equal one")
        if not self.translation_disabled or not self.terminology_translation_disabled:
            raise ValueError("Phase 3B-S requires translation and terminology translation to stay disabled")
        if self.translation_enabled or self.phase2b_translation_enabled or self.translation_api_enabled:
            raise ValueError("Phase 3B-S cannot enable a translation API")
        if self.phase3b_automatic_retry:
            raise ValueError("Phase 3B-S automatic retry must remain disabled")
        if self.phase3b_max_triple_calls != 0:
            raise ValueError("Phase 3B-S does not allow triple calls")
        if self.phase3b_max_single_calls + self.phase3b_max_pair_calls != self.phase3b_max_total_calls:
            raise ValueError("Phase 3B-S category call limits must equal the total limit")
        if self.phase3c4_automatic_retry:
            raise ValueError("Phase 3C+4 automatic retry must remain disabled")
        if self.phase3c4_maximum_content_calls != 24:
            raise ValueError("Phase 3C+4 content call limit must equal 24")
        if self.phase3c4_expected_block_count != 24:
            raise ValueError("Phase 3C+4 expected block count must equal 24")
        return self

    @field_validator("vision_api_key_env")
    @classmethod
    def validate_api_key_env(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum():
            raise ValueError("vision_api_key_env must be an environment variable name")
        return value

    @field_validator("vision_input_mode")
    @classmethod
    def validate_vision_input_mode(cls, value: str) -> str:
        if value != "base64_data_url":
            raise ValueError("Phase 2A supports base64_data_url only")
        return value

    @field_validator("vision_thinking_mode")
    @classmethod
    def validate_vision_thinking_mode(cls, value: str) -> str:
        if value not in {"enabled", "disabled"}:
            raise ValueError("vision_thinking_mode must be enabled or disabled")
        return value

    @field_validator("translation_api_key_env")
    @classmethod
    def validate_translation_api_key_env(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum():
            raise ValueError("translation_api_key_env must be an environment variable name")
        return value


def project_root() -> Path:
    """Return the repository root without relying on the current directory."""

    return Path(__file__).resolve().parents[2]


def resolve_project_path(value: str | Path, root: Path | None = None) -> Path:
    """Resolve a configured path, preserving spaces and parentheses."""

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (root or project_root()) / candidate
    return candidate.resolve(strict=False)


def load_settings(path: str | Path | None = None) -> ProjectSettings:
    """Load and validate a non-secret YAML settings file."""

    settings_path = resolve_project_path(
        path or Path("config") / "settings.example.yaml"
    )
    if not settings_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {settings_path}")
    with settings_path.open("r", encoding="utf-8") as handle:
        raw: Any = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration must be a YAML mapping: {settings_path}")
    return ProjectSettings.model_validate(raw)
