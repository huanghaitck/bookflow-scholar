"""Offline-only Phase 2A.1 normalization patch for preserved visual responses."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .io_utils import atomic_write_json, load_json, sha256_file
from .schemas import VisionBlock


VISION_NORMALIZED_SCHEMA_VERSION = "1.1"


class NormalizationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    action: str
    reason: str
    original_type: str
    original_value: Any = None
    normalized_value: Any = None
    requires_review: bool = False


BoundaryStatus = Literal[
    "known_single_page",
    "unknown_single_page",
    "needs_adjacent_context",
    "reviewed_pair",
    "reviewed_triple",
    "needs_human_review",
]


class VisionNormalizedPageV11(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = VISION_NORMALIZED_SCHEMA_VERSION
    source_model_schema_version: str | None = None
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
    continuation_from_previous: bool | None
    continuation_to_next: bool | None
    boundary_status: BoundaryStatus
    boundary_review_required: bool
    boundary_notes: str | None
    uncertain_characters: list[str]
    warnings: list[str]
    normalization_events: list[NormalizationEvent]
    raw_response_path: str
    previous_normalized_output_path: str | None
    raw_response_sha256: str
    previous_normalized_sha256: str | None
    normalized_at: datetime
    status: Literal["technical_validation_only", "needs_review"]
    authoritative: Literal[False] = False
    api_called: bool
    translation_ready: Literal[False] = False


def _model_content(raw: dict[str, Any]) -> dict[str, Any]:
    content = raw.get("response", {}).get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("Preserved raw response contains no model content string")
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        candidate = "\n".join(lines).strip()
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("Preserved model content is not one JSON object")
    return payload


def _normalize_printed_page(value: Any, events: list[NormalizationEvent]) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        normalized = str(value)
        events.append(
            NormalizationEvent(
                field="printed_page",
                action="integer_to_decimal_string",
                reason="Schema 1.1 stores printed page labels as string or null.",
                original_type="integer",
                original_value=value,
                normalized_value=normalized,
            )
        )
        return normalized
    raise ValueError("printed_page must be an integer, string, or null")


def _normalize_uncertain(value: Any, events: list[NormalizationEvent]) -> tuple[list[str], bool]:
    if value is None:
        events.append(
            NormalizationEvent(
                field="uncertain_characters",
                action="null_to_empty_list",
                reason="Schema 1.1 always stores an array.",
                original_type="null",
                original_value=None,
                normalized_value=[],
            )
        )
        return [], False
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise ValueError("uncertain_characters list items must be strings")
        return value, False
    if isinstance(value, str):
        if not value.strip():
            events.append(
                NormalizationEvent(
                    field="uncertain_characters",
                    action="blank_string_to_empty_list",
                    reason="Blank text carries no uncertain character but the field must be an array.",
                    original_type="string",
                    original_value=value,
                    normalized_value=[],
                )
            )
            return [], False
        events.append(
            NormalizationEvent(
                field="uncertain_characters",
                action="nonempty_string_to_single_item_list",
                reason="The value is preserved but its unexpected type requires review.",
                original_type="string",
                original_value=value,
                normalized_value=[value],
                requires_review=True,
            )
        )
        return [value], True
    raise ValueError("uncertain_characters must be a list, string, or null")


def _normalize_continuation(
    payload: dict[str, Any],
    field: str,
    events: list[NormalizationEvent],
    force_adjacent_review: set[str],
) -> tuple[bool | None, bool]:
    if field not in payload:
        events.append(
            NormalizationEvent(
                field=field,
                action="missing_to_null",
                reason="Missing single-page boundary evidence must not default to false.",
                original_type="missing",
                normalized_value=None,
                requires_review=True,
            )
        )
        return None, True
    value = payload[field]
    if field in force_adjacent_review:
        events.append(
            NormalizationEvent(
                field=field,
                action="single_page_decision_to_null",
                reason="Offline QA found that this boundary needs adjacent-page context.",
                original_type=type(value).__name__,
                original_value=value,
                normalized_value=None,
                requires_review=True,
            )
        )
        return None, True
    if value is None or isinstance(value, bool):
        return value, value is None
    events.append(
        NormalizationEvent(
            field=field,
            action="invalid_value_to_null",
            reason="Only true, false, or null are accepted.",
            original_type=type(value).__name__,
            original_value=value,
            normalized_value=None,
            requires_review=True,
        )
    )
    return None, True


def _normalize_blocks(value: Any, events: list[NormalizationEvent]) -> list[VisionBlock]:
    if not isinstance(value, list):
        raise ValueError("blocks must be a list")
    normalized: list[VisionBlock] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("each block must be an object")
        candidate = dict(item)
        block_id = candidate.get("block_id")
        if isinstance(block_id, int) and not isinstance(block_id, bool):
            converted = str(block_id)
            events.append(
                NormalizationEvent(
                    field=f"blocks[{index}].block_id",
                    action="integer_to_decimal_string",
                    reason="Block identifiers are strings; a decimal integer label can be converted losslessly.",
                    original_type="integer",
                    original_value=block_id,
                    normalized_value=converted,
                )
            )
            candidate["block_id"] = converted
        allowed_types = {
            "chapter_title", "section_title", "body", "footnote", "caption",
            "header", "footer", "page_number", "illustration", "unknown",
        }
        block_type = candidate.get("block_type")
        if isinstance(block_type, str) and block_type not in allowed_types:
            events.append(
                NormalizationEvent(
                    field=f"blocks[{index}].block_type",
                    action="unsupported_label_to_unknown",
                    reason="The original label is retained in this event; normalized Schema 1.1 uses unknown.",
                    original_type="string",
                    original_value=block_type,
                    normalized_value="unknown",
                )
            )
            candidate["block_type"] = "unknown"
        if candidate.get("text") is None:
            events.append(
                NormalizationEvent(
                    field=f"blocks[{index}].text",
                    action="null_to_empty_string",
                    reason="Null carries no visible characters; conversion does not invent text.",
                    original_type="null",
                    original_value=None,
                    normalized_value="",
                )
            )
            candidate["text"] = ""
        confidence = candidate.get("confidence")
        if isinstance(confidence, str):
            events.append(
                NormalizationEvent(
                    field=f"blocks[{index}].confidence",
                    action="label_to_null_score",
                    reason="A qualitative label must not be fabricated into a numeric score.",
                    original_type="string",
                    original_value=confidence,
                    normalized_value=None,
                )
            )
            candidate["confidence"] = None
        normalized.append(VisionBlock.model_validate(candidate))
    return normalized


def normalize_preserved_response_v11(
    raw_response_path: str | Path,
    output_path: str | Path,
    *,
    previous_normalized_path: str | Path | None = None,
    force_adjacent_review: set[str] | None = None,
) -> VisionNormalizedPageV11:
    """Normalize one saved response without network access or file replacement."""

    raw_path = Path(raw_response_path).resolve()
    destination = Path(output_path).resolve()
    previous = Path(previous_normalized_path).resolve() if previous_normalized_path else None
    if destination == raw_path or (previous and destination == previous):
        raise ValueError("Schema 1.1 output must use a new path")
    raw_hash_before = sha256_file(raw_path)
    previous_hash_before = sha256_file(previous) if previous and previous.is_file() else None
    raw = load_json(raw_path)
    payload = _model_content(raw)
    events: list[NormalizationEvent] = []
    printed_page = _normalize_printed_page(payload.get("printed_page"), events)
    uncertain, uncertain_review = _normalize_uncertain(
        payload.get("uncertain_characters"), events
    )
    forced = force_adjacent_review or set()
    from_previous, from_review = _normalize_continuation(
        payload, "continuation_from_previous", events, forced
    )
    to_next, to_review = _normalize_continuation(
        payload, "continuation_to_next", events, forced
    )
    needs_boundary_review = from_review or to_review
    needs_review = uncertain_review or needs_boundary_review
    blocks = _normalize_blocks(payload.get("blocks", []), events)
    warnings = payload.get("warnings") or []
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise ValueError("warnings must be a string list")
    result = VisionNormalizedPageV11(
        source_model_schema_version=(
            str(payload.get("schema_version")) if payload.get("schema_version") is not None else None
        ),
        document_id=str(payload["document_id"]),
        pdf_page=int(payload["pdf_page"]),
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        page_type=str(payload.get("page_type") or "unknown"),
        printed_page=printed_page,
        title=payload.get("title"),
        running_header=payload.get("running_header"),
        footer=payload.get("footer"),
        page_number_text=(
            str(payload["page_number_text"])
            if payload.get("page_number_text") is not None
            else None
        ),
        blocks=blocks,
        continuation_from_previous=from_previous,
        continuation_to_next=to_next,
        boundary_status=(
            "needs_adjacent_context" if needs_boundary_review else "known_single_page"
        ),
        boundary_review_required=needs_boundary_review,
        boundary_notes=payload.get("boundary_notes"),
        uncertain_characters=uncertain,
        warnings=warnings,
        normalization_events=events,
        raw_response_path=str(raw_path),
        previous_normalized_output_path=str(previous) if previous else None,
        raw_response_sha256=raw_hash_before,
        previous_normalized_sha256=previous_hash_before,
        normalized_at=datetime.now(timezone.utc),
        status=(
            "needs_review"
            if needs_review or payload.get("status") == "needs_review"
            else "technical_validation_only"
        ),
        api_called=bool(raw.get("api_called", False)),
        translation_ready=False,
    )
    atomic_write_json(destination, result)
    if sha256_file(raw_path) != raw_hash_before:
        raise RuntimeError("Preserved raw response changed during offline normalization")
    if previous and previous_hash_before and sha256_file(previous) != previous_hash_before:
        raise RuntimeError("Previous normalized result changed during Schema 1.1 normalization")
    return result
