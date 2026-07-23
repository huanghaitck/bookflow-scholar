"""Phase 1C-C: Live visual classification transport for 10-page sample.

This module performs real OpenAI-compatible API calls to classify page
structure.  It is deliberately small, sequential, and non-retryable.

Key safety guarantees:
- API key is never persisted, logged, or returned.
- Data URLs (base64 images) are never persisted.
- Results go only to ``pending_human_review``; no write-back to page_map.
- ``max_calls`` is hard-capped at 10.
- ``automatic_retry`` is always False.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from .io_utils import (
    atomic_write_json,
    sha256_file,
    sha256_text,
    stable_hash,
)
from .paths import ProjectSettings, load_settings
from .secret_store import load_api_key
from .structure_visual_schemas import (
    FORBIDDEN_RESPONSE_FIELDS,
    VisualPageClassificationResponse,
    VisualProviderRequest,
)

LIVE_RUNNER_VERSION = "phase1d_live_v2"
MAX_CALLS = 10


class ContentError(Exception):
    """Single-page content error (JSON parse, schema, page mismatch)."""


class SystemError_(Exception):
    """System-level error that stops the entire batch."""


@dataclass(frozen=True)
class LiveProviderConfig:
    provider_id: str
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float
    max_calls: int
    response_format_json_object: bool
    max_output_tokens: int
    do_sample: bool = False
    thinking_mode: str = "disabled"

    @property
    def sanitized_base_url(self) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(self.base_url)
        return parsed.hostname or self.base_url


def build_live_config(settings: ProjectSettings) -> LiveProviderConfig:
    return LiveProviderConfig(
        provider_id=settings.vision_provider,
        base_url=settings.vision_base_url,
        model=settings.vision_model,
        api_key_env=settings.vision_api_key_env,
        timeout_seconds=settings.vision_request_timeout_seconds,
        max_calls=MAX_CALLS,
        response_format_json_object=settings.vision_response_format_json_object,
        max_output_tokens=settings.vision_max_output_tokens,
        do_sample=settings.vision_do_sample,
        thinking_mode=settings.vision_thinking_mode,
    )


_PROMPT_FILE = "prompts/structure_page_classification_v1.md"


def _get_asset_ref(req: VisualProviderRequest) -> str:
    """Extract source_page_asset_ref from either request type."""
    if hasattr(req, "source_page_asset_ref"):
        return req.source_page_asset_ref
    return req.context.source_page_asset_ref


def _get_context_json(req: VisualProviderRequest) -> str:
    """Extract context_json from either request type."""
    if hasattr(req, "context_json"):
        return req.context_json
    return req.context.model_dump_json()


def _get_page_image_sha(req: VisualProviderRequest) -> str:
    """Extract page_image_sha256 from either request type."""
    if hasattr(req, "context") and hasattr(req.context, "page_image_sha256"):
        return req.context.page_image_sha256
    return ""


def _compute_live_fp_for_req(
    req: VisualProviderRequest,
    prompt_file_sha: str,
    system_prompt_sha: str,
    schema_sha: str,
    config: LiveProviderConfig,
) -> str:
    return build_live_call_fingerprint(
        offline_fingerprint=req.request_fingerprint,
        physical_page=req.physical_page,
        page_image_sha256=_get_page_image_sha(req),
        prompt_file_sha=prompt_file_sha,
        system_prompt_sha=system_prompt_sha,
        schema_sha=schema_sha,
        provider_id=config.provider_id,
        model=config.model,
        base_url=config.base_url,
        response_format_mode="json_object" if config.response_format_json_object else "none",
        extra_body_profile=f"do_sample={config.do_sample},thinking={config.thinking_mode}",
    )


def _load_prompt_file(root: Path) -> str:
    path = root / _PROMPT_FILE
    return path.read_text("utf-8")


def _build_json_template() -> str:
    return json.dumps({
        "schema_version": "1.0",
        "physical_page": 0,
        "primary_role": "unknown",
        "blank_kind": None,
        "content_features": [],
        "artifact_overlays": [],
        "original_book_content": False,
        "contains_prose": False,
        "safe_to_exclude_from_prose_flow": False,
        "requires_region_analysis": False,
        "printed_page_label": None,
        "printed_page_number": None,
        "numbering_scheme": "unknown",
        "page_side": "unknown",
        "field_evidence": [
            {
                "field_name": "primary_role",
                "observed": "visible page structure evidence",
                "basis": "visual",
                "confidence": 0.0,
            }
        ],
        "confidence_by_field": {"primary_role": 0.0},
        "warnings": [
            {
                "code": "uncertain_classification",
                "message": "Evidence is insufficient for a more specific role.",
                "severity": "warning",
            }
        ],
        "reviewer_notes": "",
        "raw_response_ref": None,
    }, indent=2)


def build_live_system_prompt(root: Path) -> str:
    prompt_text = _load_prompt_file(root)
    schema_json = VisualPageClassificationResponse.model_json_schema()
    schema_text = json.dumps(schema_json, indent=2, ensure_ascii=False)
    template_text = _build_json_template()
    parts = [
        prompt_text,
        "",
        "## Response JSON Schema",
        "",
        "```json",
        schema_text,
        "```",
        "",
        "## Response Template",
        "",
        "The following is a minimal complete template. Fill in all fields.",
        "raw_response_ref must be null (the local program overwrites it after saving).",
        "Do not include local file paths.",
        "When evidence is insufficient, use \"unknown\" or null.",
        "Do not output join_operation, structural_break, from_page, to_page, or fragment IDs.",
        "Do not decide whether prose continues across pages.",
        "Do not transcribe or translate page text.",
        "",
        "```json",
        template_text,
        "```",
    ]
    return "\n".join(parts)


def _compute_system_prompt_sha(root: Path) -> str:
    return sha256_text(build_live_system_prompt(root))


def _compute_schema_sha() -> str:
    schema_text = json.dumps(
        VisualPageClassificationResponse.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256_text(schema_text)


def _compute_prompt_file_sha(root: Path) -> str:
    return sha256_text(_load_prompt_file(root))


def build_live_call_fingerprint(
    *,
    offline_fingerprint: str,
    physical_page: int,
    page_image_sha256: str,
    prompt_file_sha: str,
    system_prompt_sha: str,
    schema_sha: str,
    provider_id: str,
    model: str,
    base_url: str,
    response_format_mode: str,
    extra_body_profile: str,
) -> str:
    return stable_hash({
        "offline_fingerprint": offline_fingerprint,
        "physical_page": physical_page,
        "page_image_sha256": page_image_sha256,
        "prompt_file_sha": prompt_file_sha,
        "system_prompt_sha": system_prompt_sha,
        "schema_sha": schema_sha,
        "provider_id": provider_id,
        "model": model,
        "base_url": base_url,
        "runner_version": LIVE_RUNNER_VERSION,
        "response_format_mode": response_format_mode,
        "extra_body_profile": extra_body_profile,
    })


_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def materialize_image_data_url(
    root: Path,
    source_page_asset_ref: str,
    expected_sha256: str,
) -> str:
    """Load an image from a project-relative path and return a data URL.

    Raises on absolute paths, path escape, missing files, or SHA mismatch.
    """
    ref = source_page_asset_ref.replace("\\", "/")
    if len(ref) >= 2 and ref[1] == ":":
        raise ValueError(f"Absolute path rejected: {ref}")
    if ref.startswith("/") or ref.startswith("\\"):
        raise ValueError(f"Absolute path rejected: {ref}")
    parts = ref.split("/")
    if ".." in parts:
        raise ValueError(f"Path escape rejected: {ref}")
    img_path = (root / ref).resolve()
    root_resolved = root.resolve()
    try:
        img_path.relative_to(root_resolved)
    except ValueError:
        raise ValueError(f"Path outside project root rejected: {ref}")
    if not img_path.is_file():
        raise FileNotFoundError(f"Image not found: {ref}")
    actual_sha = sha256_file(img_path)
    if actual_sha != expected_sha256:
        raise ValueError(
            f"Image SHA mismatch for {ref}: expected {expected_sha256[:12]}..., "
            f"got {actual_sha[:12]}..."
        )
    suffix = img_path.suffix.lower()
    mime = _MIME_MAP.get(suffix, "application/octet-stream")
    with img_path.open("rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_live_payload(
    *,
    system_prompt: str,
    context_json: str,
    image_data_url: str,
    model: str,
    max_tokens: int,
    response_format_json_object: bool,
    do_sample: bool = False,
    thinking_mode: str = "disabled",
) -> dict[str, Any]:
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": context_json},
        {
            "type": "image_url",
            "image_url": {"url": image_data_url},
        },
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
    }
    if response_format_json_object:
        payload["response_format"] = {"type": "json_object"}
    extra_body: dict[str, Any] = {
        "do_sample": do_sample,
        "thinking": {"type": thinking_mode},
    }
    payload["extra_body"] = extra_body
    return payload


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported response type: {type(value).__name__}")


def extract_chat_completion_result(raw_response: Any) -> dict[str, Any]:
    raw = _as_dict(raw_response)
    choices = raw.get("choices") or []
    if not choices:
        raise SystemError_("Provider response contains no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise SystemError_("Provider response message content is empty or not a string")
    usage_raw = raw.get("usage")
    usage = _as_dict(usage_raw) if usage_raw is not None else None
    provider_request_id = raw.get("id") or getattr(raw_response, "_request_id", None)
    model_returned = raw.get("model")
    finish_reason = choices[0].get("finish_reason")
    created = raw.get("created")
    return {
        "content": content,
        "usage": usage,
        "provider_request_id": str(provider_request_id) if provider_request_id else None,
        "model": str(model_returned) if model_returned else None,
        "finish_reason": finish_reason,
        "created": created,
    }


_SENSITIVE_PATTERNS = [
    "authorization",
    "api_key",
    "apikey",
    "bearer",
    "data:image/",
    "base64,",
]


def sanitize_raw_response(raw_dict: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in raw_dict.items():
        key_lower = key.lower()
        if any(pat in key_lower for pat in _SENSITIVE_PATTERNS):
            cleaned[key] = "[REDACTED]"
            continue
        if isinstance(value, str):
            if "data:image/" in value.lower() or "base64," in value.lower():
                cleaned[key] = "[REDACTED]"
                continue
            cleaned[key] = value
        elif isinstance(value, dict):
            cleaned[key] = sanitize_raw_response(value)
        elif isinstance(value, list):
            cleaned[key] = [
                sanitize_raw_response(v) if isinstance(v, dict)
                else ("[REDACTED]" if isinstance(v, str) and any(pat in v.lower() for pat in _SENSITIVE_PATTERNS) else v)
                for v in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _assert_no_secrets(text: str, context: str) -> None:
    lower = text.lower()
    for pat in ["authorization", "bearer ", "data:image/", "base64,"]:
        if pat in lower:
            raise SystemError_(f"Sensitive data detected in {context}: pattern '{pat}'")


def _process_model_response(
    content: str,
    physical_page: int,
) -> VisualPageClassificationResponse:
    # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
    stripped = content.strip()
    if stripped.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        else:
            stripped = stripped[3:]
        # Remove closing fence
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()
    # Handle literal "null" string
    if stripped == "null":
        raise ContentError("Response is literal 'null' string, expected JSON object")
    # Handle empty content
    if not stripped:
        raise ContentError("Response content is empty")
    try:
        response_dict = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ContentError(f"Response is not valid JSON: {exc}") from exc
    if not isinstance(response_dict, dict):
        raise ContentError("Response JSON is not an object")
    # Remove null values from confidence_by_field (model may put null for unassessable fields)
    cbf = response_dict.get("confidence_by_field")
    if isinstance(cbf, dict):
        response_dict["confidence_by_field"] = {
            k: v for k, v in cbf.items() if v is not None
        }
    for field_name in FORBIDDEN_RESPONSE_FIELDS:
        if field_name in response_dict:
            raise ContentError(f"Response contains forbidden field '{field_name}'")
    response_dict["raw_response_ref"] = None
    resp_page = response_dict.get("physical_page")
    if resp_page != physical_page:
        raise ContentError(
            f"Response physical_page ({resp_page}) does not match request ({physical_page})"
        )
    try:
        response = VisualPageClassificationResponse.model_validate(response_dict)
    except Exception as exc:
        raise ContentError(f"Schema validation failed: {exc}") from exc
    return response


@dataclass
class LedgerEntry:
    request_id: str
    physical_page: int
    offline_request_fingerprint: str
    live_call_fingerprint: str
    status: str
    attempted: bool
    api_call_count: int
    automatic_retry_count: int
    provider_request_id: str | None = None
    model: str | None = None
    normalized_response_ref: str | None = None
    raw_response_ref: str | None = None
    usage_ref: str | None = None
    error_ref: str | None = None
    review_status: str = "pending_human_review"
    called_at: str | None = None
    updated_at: str | None = None


def load_call_ledger(path: Path) -> dict[int, LedgerEntry]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text("utf-8"))
    entries: dict[int, LedgerEntry] = {}
    for item in data.get("entries", []):
        entry = LedgerEntry(**item)
        entries[entry.physical_page] = entry
    return entries


def save_call_ledger(path: Path, entries: dict[int, LedgerEntry]) -> None:
    data = {
        "entries": [
            {k: v for k, v in entry.__dict__.items()}
            for entry in sorted(entries.values(), key=lambda e: e.physical_page)
        ],
    }
    atomic_write_json(path, data)


class OpenAICompatibleLiveTransport:
    """Real OpenAI-compatible transport with max_retries=0."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        client_factory: Callable[..., Any] = OpenAI,
    ) -> None:
        self._client = client_factory(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def call(self, payload: dict[str, Any]) -> Any:
        return self._client.chat.completions.create(**payload)


@dataclass
class BatchRunResult:
    planned_pages: list[int]
    already_successful: list[int]
    pending_at_start: list[int]
    attempted: list[int]
    succeeded: list[int]
    content_errors: list[int]
    system_errors: list[int]
    skipped_existing: list[int]
    not_attempted: list[int]
    actual_api_calls: int
    automatic_retry_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    stopped_due_to_system_error: bool
    preflight_ok: bool
    preflight_summary: str


def _page_filename(page: int) -> str:
    return f"page_{page:04d}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_preflight(
    *,
    root: Path,
    config: LiveProviderConfig,
    requests: list[VisualProviderRequest],
    ledger: dict[int, LedgerEntry],
    prompt_file_sha: str,
    system_prompt_sha: str,
    schema_sha: str,
) -> tuple[bool, str]:
    planned_pages = [r.physical_page for r in requests]
    expected_pages = [1, 8, 119, 120, 198, 340, 380, 384, 401, 412]
    errors: list[str] = []
    checks: list[str] = []

    if len(requests) != 10:
        errors.append(f"Expected 10 requests, got {len(requests)}")
    else:
        checks.append("10 offline requests")

    if sorted(planned_pages) != sorted(expected_pages):
        errors.append(f"Page mismatch: {sorted(planned_pages)} != {sorted(expected_pages)}")
    else:
        checks.append("page numbers match")

    if len(planned_pages) != len(set(planned_pages)):
        errors.append("Duplicate page numbers")
    else:
        checks.append("no duplicates")

    prompt_path = root / _PROMPT_FILE
    if not prompt_path.is_file():
        errors.append("Prompt file missing")
    else:
        checks.append("prompt file exists")

    img_ok = True
    for req in requests:
        ref = _get_asset_ref(req)
        img_path = root / ref
        if not img_path.is_file():
            errors.append(f"Image missing: p{req.physical_page}")
            img_ok = False
            continue
        actual = sha256_file(img_path)
        expected = _get_page_image_sha(req)
        if actual != expected:
            errors.append(f"Image SHA mismatch: p{req.physical_page}")
            img_ok = False
    if img_ok:
        checks.append("all 10 images verified")

    if not config.base_url:
        errors.append("base_url missing")
    else:
        checks.append(f"base_url={config.sanitized_base_url}")
    if not config.model:
        errors.append("model missing")
    else:
        checks.append(f"model={config.model}")

    try:
        settings = load_settings()
        key_value, key_name = load_api_key(settings, root)
        if key_value:
            checks.append(f"api_key loaded from {key_name}")
        else:
            errors.append("API key empty")
    except Exception as exc:
        errors.append(f"API key load failed: {exc}")

    already = sum(
        1 for r in requests
        if ledger.get(r.physical_page) and ledger[r.physical_page].status == "success"
    )
    checks.append(f"already_successful={already}")
    pending = len(planned_pages) - already
    checks.append(f"pending={pending}")
    if pending > MAX_CALLS:
        errors.append(f"Pending {pending} > max_calls {MAX_CALLS}")

    base_dir = root / "data/fullbook/structure/phase1c/live/sample_batch_v1"
    for sub in ["raw", "normalized", "errors", "usage", "request_metadata"]:
        d = base_dir / sub
        try:
            d.mkdir(parents=True, exist_ok=True)
            test_file = d / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
        except Exception as exc:
            errors.append(f"Cannot write to {d}: {exc}")
    checks.append("output dirs writable")

    for f in [
        "data/fullbook/structure/registry/page_map.jsonl",
        "data/fullbook/structure/bridges/bridge_candidates.jsonl",
    ]:
        if not (root / f).is_file():
            errors.append(f"Frozen file missing: {f}")
    checks.append("frozen files readable")

    summary_parts = [
        f"provider={config.provider_id}",
        f"base_url={config.sanitized_base_url}",
        f"model={config.model}",
        f"planned={planned_pages}",
        f"already_success={already}",
        f"pending={pending}",
        f"max_calls={MAX_CALLS}",
        f"prompt_sha={prompt_file_sha[:12]}",
        f"system_prompt_sha={system_prompt_sha[:12]}",
        f"schema_sha={schema_sha[:12]}",
        f"runner={LIVE_RUNNER_VERSION}",
    ]
    summary = " | ".join(summary_parts)
    if errors:
        summary += " | ERRORS: " + "; ".join(errors)
        return False, summary
    return True, summary


def run_live_sample_batch(
    *,
    root: Path,
    settings: ProjectSettings,
    config: LiveProviderConfig,
    requests: list[VisualProviderRequest],
    ledger: dict[int, LedgerEntry],
    prompt_file_sha: str,
    system_prompt_sha: str,
    schema_sha: str,
    client_factory: Callable[..., Any] = OpenAI,
    dry_run: bool = False,
    system_prompt: str | None = None,
    api_key: str | None = None,
    base_dir: Path | None = None,
) -> BatchRunResult:
    if system_prompt is None:
        system_prompt = build_live_system_prompt(root)
    if base_dir is None:
        base_dir = root / "data/fullbook/structure/phase1c/live/sample_batch_v1"
    for _sub in ("raw", "normalized", "errors", "usage", "request_metadata"):
        (base_dir / _sub).mkdir(parents=True, exist_ok=True)
    _base_rel = str(base_dir.relative_to(root)).replace("\\", "/")

    planned_pages = [r.physical_page for r in requests]
    already_successful: list[int] = []
    pending_at_start: list[int] = []

    for req in requests:
        entry = ledger.get(req.physical_page)
        current_fp = _compute_live_fp_for_req(req, prompt_file_sha, system_prompt_sha, schema_sha, config)
        if entry and entry.status == "success" and entry.live_call_fingerprint == current_fp:
            already_successful.append(req.physical_page)
        else:
            pending_at_start.append(req.physical_page)

    result = BatchRunResult(
        planned_pages=planned_pages,
        already_successful=already_successful,
        pending_at_start=pending_at_start,
        attempted=[],
        succeeded=[],
        content_errors=[],
        system_errors=[],
        skipped_existing=already_successful,
        not_attempted=[],
        actual_api_calls=0,
        automatic_retry_count=0,
        total_prompt_tokens=0,
        total_completion_tokens=0,
        total_tokens=0,
        stopped_due_to_system_error=False,
        preflight_ok=True,
        preflight_summary="",
    )

    if dry_run:
        return result

    if api_key is None:
        api_key, _ = load_api_key(settings, root)
    transport = OpenAICompatibleLiveTransport(
        api_key=api_key,
        base_url=config.base_url,
        timeout_seconds=config.timeout_seconds,
        client_factory=client_factory,
    )

    batch_stopped = False

    for req in requests:
        pg = req.physical_page
        entry = ledger.get(pg)
        current_fp = _compute_live_fp_for_req(req, prompt_file_sha, system_prompt_sha, schema_sha, config)
        if entry and entry.status == "success" and entry.live_call_fingerprint == current_fp:
            continue

        if batch_stopped:
            result.not_attempted.append(pg)
            ledger[pg] = LedgerEntry(
                request_id=req.request_id,
                physical_page=pg,
                offline_request_fingerprint=req.request_fingerprint,
                live_call_fingerprint="",
                status="not_attempted_after_system_stop",
                attempted=False,
                api_call_count=0,
                automatic_retry_count=0,
                updated_at=_now_iso(),
            )
            save_call_ledger(base_dir / "call_ledger.json", ledger)
            continue

        live_fp = build_live_call_fingerprint(
            offline_fingerprint=req.request_fingerprint,
            physical_page=pg,
            page_image_sha256=req.context.page_image_sha256,
            prompt_file_sha=prompt_file_sha,
            system_prompt_sha=system_prompt_sha,
            schema_sha=schema_sha,
            provider_id=config.provider_id,
            model=config.model,
            base_url=config.base_url,
            response_format_mode="json_object" if config.response_format_json_object else "none",
            extra_body_profile=f"do_sample={config.do_sample},thinking={config.thinking_mode}",
        )

        ledger[pg] = LedgerEntry(
            request_id=req.request_id,
            physical_page=pg,
            offline_request_fingerprint=req.request_fingerprint,
            live_call_fingerprint=live_fp,
            status="pending",
            attempted=True,
            api_call_count=0,
            automatic_retry_count=0,
            called_at=_now_iso(),
            updated_at=_now_iso(),
        )
        save_call_ledger(base_dir / "call_ledger.json", ledger)

        result.attempted.append(pg)
        result.actual_api_calls += 1

        try:
            image_data_url = materialize_image_data_url(
                root,
                _get_asset_ref(req),
                _get_page_image_sha(req),
            )

            payload = build_live_payload(
                system_prompt=system_prompt,
                context_json=_get_context_json(req),
                image_data_url=image_data_url,
                model=config.model,
                max_tokens=config.max_output_tokens,
                response_format_json_object=config.response_format_json_object,
                do_sample=config.do_sample,
                thinking_mode=config.thinking_mode,
            )

            raw_response = transport.call(payload)
            extracted = extract_chat_completion_result(raw_response)
            content = extracted["content"]

            raw_dict = _as_dict(raw_response)
            sanitized_raw = sanitize_raw_response(raw_dict)
            _assert_no_secrets(
                json.dumps(sanitized_raw, ensure_ascii=False),
                "raw response",
            )
            sanitized_raw["received_at"] = _now_iso()
            raw_path = base_dir / "raw" / f"{_page_filename(pg)}.json"
            atomic_write_json(raw_path, sanitized_raw)

            usage = extracted.get("usage") or {}
            usage_path = base_dir / "usage" / f"{_page_filename(pg)}.json"
            atomic_write_json(usage_path, usage)

            metadata = {
                "request_id": req.request_id,
                "physical_page": pg,
                "offline_request_fingerprint": req.request_fingerprint,
                "live_call_fingerprint": live_fp,
                "source_page_asset_ref": _get_asset_ref(req),
                "page_image_sha256": _get_page_image_sha(req),
                "prompt_sha256": prompt_file_sha,
                "system_prompt_sha256": system_prompt_sha,
                "schema_sha256": schema_sha,
                "provider_id": config.provider_id,
                "sanitized_base_url": config.sanitized_base_url,
                "model": config.model,
                "called_at": _now_iso(),
                "status": "success",
            }
            meta_path = base_dir / "request_metadata" / f"{_page_filename(pg)}.json"
            atomic_write_json(meta_path, metadata)

            response = _process_model_response(content, pg)
            raw_ref = f"{_base_rel}/raw/{_page_filename(pg)}.json"
            response = response.model_copy(update={"raw_response_ref": raw_ref})

            norm_path = base_dir / "normalized" / f"{_page_filename(pg)}.json"
            norm_data = {
                "response": response.model_dump(mode="json"),
                "review_status": "pending_human_review",
                "live_call_fingerprint": live_fp,
            }
            atomic_write_json(norm_path, norm_data)

            result.succeeded.append(pg)
            result.total_prompt_tokens += usage.get("prompt_tokens", 0) or 0
            result.total_completion_tokens += usage.get("completion_tokens", 0) or 0
            result.total_tokens += usage.get("total_tokens", 0) or 0

            ledger[pg] = LedgerEntry(
                request_id=req.request_id,
                physical_page=pg,
                offline_request_fingerprint=req.request_fingerprint,
                live_call_fingerprint=live_fp,
                status="success",
                attempted=True,
                api_call_count=1,
                automatic_retry_count=0,
                provider_request_id=extracted.get("provider_request_id"),
                model=extracted.get("model") or config.model,
                normalized_response_ref=f"{_base_rel}/normalized/{_page_filename(pg)}.json",
                raw_response_ref=raw_ref,
                usage_ref=f"{_base_rel}/usage/{_page_filename(pg)}.json",
                review_status="pending_human_review",
                called_at=_now_iso(),
                updated_at=_now_iso(),
            )
            save_call_ledger(base_dir / "call_ledger.json", ledger)

        except SystemError_ as exc:
            result.system_errors.append(pg)
            result.stopped_due_to_system_error = True
            batch_stopped = True
            error_data = {
                "physical_page": pg,
                "error_type": "system_error",
                "message": str(exc),
                "occurred_at": _now_iso(),
            }
            _assert_no_secrets(str(exc), "system error message")
            error_path = base_dir / "errors" / f"{_page_filename(pg)}.json"
            atomic_write_json(error_path, error_data)
            ledger[pg] = LedgerEntry(
                request_id=req.request_id,
                physical_page=pg,
                offline_request_fingerprint=req.request_fingerprint,
                live_call_fingerprint=live_fp,
                status="system_error",
                attempted=True,
                api_call_count=1,
                automatic_retry_count=0,
                error_ref=f"{_base_rel}/errors/{_page_filename(pg)}.json",
                called_at=_now_iso(),
                updated_at=_now_iso(),
            )
            save_call_ledger(base_dir / "call_ledger.json", ledger)

        except ContentError as exc:
            result.content_errors.append(pg)
            error_data = {
                "physical_page": pg,
                "error_type": "content_error",
                "message": str(exc),
                "occurred_at": _now_iso(),
            }
            error_path = base_dir / "errors" / f"{_page_filename(pg)}.json"
            atomic_write_json(error_path, error_data)
            ledger[pg] = LedgerEntry(
                request_id=req.request_id,
                physical_page=pg,
                offline_request_fingerprint=req.request_fingerprint,
                live_call_fingerprint=live_fp,
                status="content_error",
                attempted=True,
                api_call_count=1,
                automatic_retry_count=0,
                error_ref=f"{_base_rel}/errors/{_page_filename(pg)}.json",
                called_at=_now_iso(),
                updated_at=_now_iso(),
            )
            save_call_ledger(base_dir / "call_ledger.json", ledger)

        except Exception as exc:
            result.system_errors.append(pg)
            result.stopped_due_to_system_error = True
            batch_stopped = True
            error_data = {
                "physical_page": pg,
                "error_type": "system_error",
                "message": str(exc),
                "occurred_at": _now_iso(),
            }
            _assert_no_secrets(str(exc), "unexpected error message")
            error_path = base_dir / "errors" / f"{_page_filename(pg)}.json"
            atomic_write_json(error_path, error_data)
            ledger[pg] = LedgerEntry(
                request_id=req.request_id,
                physical_page=pg,
                offline_request_fingerprint=req.request_fingerprint,
                live_call_fingerprint=live_fp,
                status="system_error",
                attempted=True,
                api_call_count=1,
                automatic_retry_count=0,
                error_ref=f"{_base_rel}/errors/{_page_filename(pg)}.json",
                called_at=_now_iso(),
                updated_at=_now_iso(),
            )
            save_call_ledger(base_dir / "call_ledger.json", ledger)

    return result


def write_live_run_summary(
    path: Path,
    *,
    config: LiveProviderConfig,
    result: BatchRunResult,
    prompt_sha: str,
    schema_sha: str,
    started_at: str,
    completed_at: str,
) -> None:
    summary = {
        "batch_id": "phase1c_c_live_sample_v1",
        "live_runner_version": LIVE_RUNNER_VERSION,
        "provider_id": config.provider_id,
        "sanitized_base_url": config.sanitized_base_url,
        "model": config.model,
        "planned_pages": result.planned_pages,
        "already_successful_pages": result.already_successful,
        "pending_pages_at_start": result.pending_at_start,
        "attempted_pages": result.attempted,
        "succeeded_pages": result.succeeded,
        "content_error_pages": result.content_errors,
        "system_error_pages": result.system_errors,
        "skipped_existing_success_pages": result.skipped_existing,
        "not_attempted_pages": result.not_attempted,
        "planned_call_count": len(result.pending_at_start),
        "actual_api_call_count": result.actual_api_calls,
        "max_calls": MAX_CALLS,
        "automatic_retry_count": result.automatic_retry_count,
        "total_prompt_tokens": result.total_prompt_tokens,
        "total_completion_tokens": result.total_completion_tokens,
        "total_tokens": result.total_tokens,
        "prompt_sha256": prompt_sha,
        "schema_sha256": schema_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "stopped_due_to_system_error": result.stopped_due_to_system_error,
        "review_status": "pending_human_review",
        "formal_structure_modified": False,
        "boundaries_modified": False,
        "api_key_logged": False,
        "data_url_persisted": False,
    }
    atomic_write_json(path, summary)
