"""Phase 2A preflight, one-call execution, normalization, and cache guards."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from pydantic import BaseModel, Field, ValidationError

from .io_utils import atomic_write_json, load_json, sha256_file, sha256_text, stable_hash
from .page_pipeline import build_context, page_status
from .paths import ProjectSettings, project_root, resolve_project_path
from .schemas import (
    PageRecord,
    VISION_SCHEMA_VERSION,
    VisionModelPayload,
    VisionPageResult,
)
from .secret_store import api_key_status, load_api_key
from .vision_provider import ZhipuOpenAICompatibleProvider


class VisionPreflightReport(BaseModel):
    provider: str
    model: str
    base_url: str
    api_key_env: str
    api_key_set: bool
    source_pdf: str
    source_pdf_sha256: str
    document_id: str
    pdf_page: int
    actual_page_count: int
    image_path: str
    image_sha256: str
    image_width: int
    image_height: int
    image_size_bytes: int
    manifest_path: str
    manifest_complete: bool
    prompt_path: str
    prompt_version: str
    prompt_sha256: str
    schema_version: str
    request_fingerprint: str
    cache_hit: bool
    cached_status: str | None = None
    maximum_real_calls: int
    real_calls_already_started: int
    remaining_real_calls: int
    expected_real_calls: int
    input_page_count: int = 1
    max_output_tokens: int
    visual_input_tokens_estimate: str
    temperature: float
    do_sample: bool
    thinking_mode: str
    input_mode: str
    response_format_json_object: bool
    automatic_retry: bool
    api_enabled_by_config: bool
    translation_calls: int = 0
    deepseek_enabled: bool
    full_pdf_processing: bool = False
    full_pdf_protection: bool
    automatic_phase_advance: bool
    maximum_cash_cost_cny: float
    conservative_cash_cost_upper_bound_cny: float
    pricing_reference_url: str
    pricing_checked_date: str
    cash_risk_message: str
    blockers: list[str] = Field(default_factory=list)
    ready_for_real_call: bool
    offline: bool = True
    api_called: bool = False


class VisionCallResult(BaseModel):
    status: str
    request_fingerprint: str
    pdf_page: int
    api_called: bool
    cache_hit: bool
    real_calls_started_this_run: int
    retries: int = 0
    request_id: str | None = None
    usage: dict[str, Any] | None = None
    raw_response_path: str | None = None
    normalized_output_path: str | None = None
    validation_path: str | None = None
    usage_path: str | None = None
    request_metadata_path: str | None = None
    authoritative: bool = False
    translation_ready: bool = False
    elapsed_seconds: float = 0


class VisionCallFailed(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        http_status: int | None,
        raw_response_path: str,
        usage_available: bool,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.http_status = http_status
        self.raw_response_path = raw_response_path
        self.usage_available = usage_available


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.") or "value"


def _vision_paths(
    settings: ProjectSettings,
    root: Path,
    provider: str,
    model: str,
    page: int,
    fingerprint: str,
) -> dict[str, Path]:
    provider_part = _safe_component(provider)
    model_part = _safe_component(model)
    page_part = f"page_{page:04d}"
    raw_root = resolve_project_path(settings.vision_raw_directory, root=root)
    normalized_root = resolve_project_path(settings.vision_normalized_directory, root=root)
    request_root = resolve_project_path(settings.vision_request_directory, root=root)
    usage_root = resolve_project_path(settings.vision_usage_directory, root=root)
    cache_root = resolve_project_path(settings.vision_cache_directory, root=root)
    return {
        "raw": raw_root / provider_part / model_part / page_part / f"{fingerprint}.json",
        "error": raw_root
        / provider_part
        / model_part
        / page_part
        / f"{fingerprint}.error.json",
        "normalized": normalized_root
        / provider_part
        / model_part
        / page_part
        / f"{fingerprint}.json",
        "validation": normalized_root
        / provider_part
        / model_part
        / page_part
        / f"{fingerprint}.validation.json",
        "request": request_root / "records" / f"{fingerprint}.json",
        "ledger": request_root / "phase2a_call_ledger.json",
        "lock": request_root / "phase2a_call_ledger.lock",
        "usage": usage_root / f"{fingerprint}.json",
        "cache": cache_root / f"{fingerprint}.json",
    }


def _load_ledger(path: Path, maximum: int) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": "1.0",
            "phase": "2A",
            "maximum_real_calls": maximum,
            "real_calls_started": 0,
            "attempts": [],
        }
    ledger = load_json(path)
    if not isinstance(ledger, dict):
        raise RuntimeError("Phase 2A call ledger is invalid")
    return ledger


@contextmanager
def _exclusive_call_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            "Another Phase 2A call may be in progress; manual review is required"
        ) from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        if path.exists():
            path.unlink()


def _page_record(context, page: int) -> PageRecord:
    record_path = Path(context.record_directory) / f"page_{page:04d}.json"
    if not record_path.is_file():
        raise FileNotFoundError(f"Page record not found: {record_path}")
    return PageRecord.model_validate(load_json(record_path))


def _request_fingerprint(
    *,
    provider: str,
    model: str,
    base_url: str,
    document_id: str,
    pdf_page: int,
    image_sha256: str,
    prompt_version: str,
    prompt_sha256: str,
    settings: ProjectSettings,
) -> str:
    return stable_hash(
        {
            "phase": "2A",
            "provider": provider,
            "model": model,
            "base_url": base_url.rstrip("/"),
            "document_id": document_id,
            "pdf_page": pdf_page,
            "image_sha256": image_sha256,
            "prompt_version": prompt_version,
            "prompt_sha256": prompt_sha256,
            "schema_version": VISION_SCHEMA_VERSION,
            "max_output_tokens": settings.vision_max_output_tokens,
            "temperature": settings.vision_temperature,
            "do_sample": settings.vision_do_sample,
            "thinking_mode": settings.vision_thinking_mode,
            "input_mode": settings.vision_input_mode,
            "response_format_json_object": settings.vision_response_format_json_object,
            "automatic_retry": settings.vision_automatic_retry,
        }
    )


def vision_preflight(
    pdf_path: str | Path,
    page: int,
    settings: ProjectSettings,
    *,
    provider: str | None = None,
    model: str | None = None,
    root: Path | None = None,
) -> VisionPreflightReport:
    """Run a fully offline single-page API preflight."""

    root = (root or project_root()).resolve()
    selected_provider = provider or settings.vision_provider
    selected_model = model or settings.vision_model
    context = build_context(
        pdf_path,
        settings,
        pages=[page],
        dpi=settings.render_dpi,
        color_mode=settings.render_color_mode,
        image_format=settings.render_format,
        root=root,
    )
    status = page_status(
        pdf_path,
        settings,
        pages=[page],
        dpi=settings.render_dpi,
        color_mode=settings.render_color_mode,
        image_format=settings.render_format,
        root=root,
    )
    record = _page_record(context, page)
    image_path = Path(record.image_path)
    prompt_path = resolve_project_path(settings.vision_prompt_path, root=root)
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Vision prompt not found: {prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")
    prompt_hash = sha256_text(prompt_text)
    fingerprint = _request_fingerprint(
        provider=selected_provider,
        model=selected_model,
        base_url=settings.vision_base_url,
        document_id=context.document_id,
        pdf_page=page,
        image_sha256=record.image_sha256,
        prompt_version=settings.vision_prompt_version,
        prompt_sha256=prompt_hash,
        settings=settings,
    )
    paths = _vision_paths(
        settings, root, selected_provider, selected_model, page, fingerprint
    )
    cache_hit = False
    cached_status: str | None = None
    if paths["cache"].is_file():
        cache = load_json(paths["cache"])
        cache_hit = bool(
            isinstance(cache, dict)
            and cache.get("request_fingerprint") == fingerprint
            and cache.get("api_call_completed") is True
            and Path(str(cache.get("raw_response_path", ""))).is_file()
            and Path(str(cache.get("normalized_output_path", ""))).is_file()
        )
        cached_status = str(cache.get("status")) if cache_hit else None
    ledger = _load_ledger(paths["ledger"], settings.vision_maximum_real_calls)
    started = int(ledger.get("real_calls_started", 0))
    remaining = max(0, settings.vision_maximum_real_calls - started)
    key_set, _ = api_key_status(settings, root)
    blockers: list[str] = []
    if selected_provider != settings.vision_provider:
        blockers.append("Requested provider does not match configuration")
    if selected_model != settings.vision_model:
        blockers.append("Requested model does not match configuration")
    if not status.ready_for_vision:
        blockers.append("Rendered page or manifest validation failed")
    if sha256_file(image_path) != record.image_sha256:
        blockers.append("Page image hash does not match its manifest record")
    if not settings.full_pdf_protection:
        blockers.append("Full PDF protection is disabled")
    if settings.translation_enabled:
        blockers.append("Translation must be disabled in Phase 2A")
    if settings.automatic_phase_advance:
        blockers.append("Automatic phase advance must be disabled")
    if settings.vision_automatic_retry:
        blockers.append("Automatic retry must be disabled")
    if settings.vision_maximum_real_calls != 1:
        blockers.append("Phase 2A maximum real calls must equal one")
    if settings.vision_max_output_tokens > 8000:
        blockers.append("Maximum output tokens exceeds the Phase 2A limit")
    if settings.vision_maximum_cash_cost_cny > 0.50:
        blockers.append("Configured cash-risk ceiling exceeds 0.50 CNY")
    if settings.vision_input_mode != "base64_data_url":
        blockers.append("Phase 2A requires Base64 Data URL image input")
    if settings.vision_do_sample:
        blockers.append("Phase 2A deterministic transcription requires do_sample=false")
    if settings.vision_thinking_mode != "disabled":
        blockers.append("Phase 2A requires thinking mode to be disabled")
    conservative_cost = round(
        (
            settings.vision_context_window_tokens
            * settings.vision_input_price_cny_per_million_tokens_upper
            + settings.vision_max_output_tokens
            * settings.vision_output_price_cny_per_million_tokens_upper
        )
        / 1_000_000,
        6,
    )
    if conservative_cost > settings.vision_maximum_cash_cost_cny:
        blockers.append("Conservative configured cash-cost bound exceeds the Phase 2A ceiling")
    if not cache_hit and not key_set:
        blockers.append(
            f"API key is not configured in {settings.vision_api_key_env}"
        )
    if not cache_hit and remaining < 1:
        blockers.append("The persistent Phase 2A real-call limit is already exhausted")
    expected_calls = 0 if cache_hit else 1
    return VisionPreflightReport(
        provider=selected_provider,
        model=selected_model,
        base_url=settings.vision_base_url,
        api_key_env=settings.vision_api_key_env,
        api_key_set=key_set,
        source_pdf=context.source_pdf,
        source_pdf_sha256=context.source_pdf_sha256,
        document_id=context.document_id,
        pdf_page=page,
        actual_page_count=context.page_count,
        image_path=str(image_path.resolve()),
        image_sha256=record.image_sha256,
        image_width=record.image_width,
        image_height=record.image_height,
        image_size_bytes=image_path.stat().st_size,
        manifest_path=context.manifest_path,
        manifest_complete=status.manifest_complete,
        prompt_path=str(prompt_path.resolve()),
        prompt_version=settings.vision_prompt_version,
        prompt_sha256=prompt_hash,
        schema_version=VISION_SCHEMA_VERSION,
        request_fingerprint=fingerprint,
        cache_hit=cache_hit,
        cached_status=cached_status,
        maximum_real_calls=settings.vision_maximum_real_calls,
        real_calls_already_started=started,
        remaining_real_calls=remaining,
        expected_real_calls=expected_calls,
        max_output_tokens=settings.vision_max_output_tokens,
        visual_input_tokens_estimate=(
            "Cannot be predicted reliably before the visual API response; usage must be recorded."
        ),
        temperature=settings.vision_temperature,
        do_sample=settings.vision_do_sample,
        thinking_mode=settings.vision_thinking_mode,
        input_mode=settings.vision_input_mode,
        response_format_json_object=settings.vision_response_format_json_object,
        automatic_retry=settings.vision_automatic_retry,
        api_enabled_by_config=settings.vision_api_enabled,
        deepseek_enabled=settings.translation_enabled,
        full_pdf_protection=settings.full_pdf_protection,
        automatic_phase_advance=settings.automatic_phase_advance,
        maximum_cash_cost_cny=settings.vision_maximum_cash_cost_cny,
        conservative_cash_cost_upper_bound_cny=conservative_cost,
        pricing_reference_url=settings.vision_pricing_reference_url,
        pricing_checked_date=settings.vision_pricing_checked_date,
        cash_risk_message=(
            f"Configured conservative public-price bound is CNY {conservative_cost:.3f}; "
            "the request may use an existing token package or may cause cash billing. "
            "The API may not expose package balance; check the Zhipu console before and after."
        ),
        blockers=blockers,
        ready_for_real_call=not blockers,
    )


def _extract_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Model content is not a JSON object")
    return parsed


def _validation_errors(exc: Exception) -> list[dict[str, Any]]:
    if isinstance(exc, ValidationError):
        return exc.errors(include_input=False, include_url=False)
    return [{"type": type(exc).__name__, "loc": [], "msg": str(exc)[:500]}]


def _normalize_response(
    *,
    content: str,
    preflight: VisionPreflightReport,
    raw_path: Path,
    normalized_path: Path,
    validation_path: Path,
) -> VisionPageResult:
    try:
        parsed = _extract_json_object(content)
        payload = VisionModelPayload.model_validate(parsed)
        mismatches: list[str] = []
        if payload.document_id != preflight.document_id:
            mismatches.append("document_id")
        if payload.pdf_page != preflight.pdf_page:
            mismatches.append("pdf_page")
        if payload.provider != preflight.provider:
            mismatches.append("provider")
        if payload.model != preflight.model:
            mismatches.append("model")
        if payload.schema_version != VISION_SCHEMA_VERSION:
            mismatches.append("schema_version")
        if mismatches:
            raise ValueError(
                "Model identity fields do not match request: " + ", ".join(mismatches)
            )
        normalized = VisionPageResult(
            schema_version=VISION_SCHEMA_VERSION,
            document_id=payload.document_id,
            pdf_page=payload.pdf_page,
            provider=payload.provider,
            model=payload.model,
            source_method="vision_api_image",
            source_image=preflight.image_path,
            source_image_sha256=preflight.image_sha256,
            page_type=payload.page_type,
            printed_page=payload.printed_page,
            title=payload.title,
            running_header=payload.running_header,
            footer=payload.footer,
            page_number_text=payload.page_number_text,
            blocks=payload.blocks,
            continuation_from_previous=payload.continuation_from_previous,
            continuation_to_next=payload.continuation_to_next,
            boundary_notes=payload.boundary_notes,
            uncertain_characters=payload.uncertain_characters,
            warnings=payload.warnings,
            raw_response_path=str(raw_path.resolve()),
            normalized_output_path=str(normalized_path.resolve()),
            input_fingerprint=preflight.request_fingerprint,
            status=payload.status,
            authoritative=False,
            api_called=True,
            translation_ready=False,
        )
        atomic_write_json(
            validation_path,
            {
                "schema_version": VISION_SCHEMA_VERSION,
                "valid": True,
                "errors": [],
                "unknown_fields_silently_removed": False,
                "transcription_text_modified": False,
            },
        )
        return normalized
    except Exception as exc:
        errors = _validation_errors(exc)
        fallback = VisionPageResult(
            schema_version=VISION_SCHEMA_VERSION,
            document_id=preflight.document_id,
            pdf_page=preflight.pdf_page,
            provider=preflight.provider,
            model=preflight.model,
            source_method="vision_api_image",
            source_image=preflight.image_path,
            source_image_sha256=preflight.image_sha256,
            page_type="unknown",
            printed_page=None,
            title=None,
            running_header=None,
            footer=None,
            page_number_text=None,
            blocks=[],
            continuation_from_previous=None,
            continuation_to_next=None,
            boundary_notes="Local schema validation failed; inspect the preserved raw response.",
            uncertain_characters=[],
            warnings=["Model JSON could not be normalized without altering content."],
            raw_response_path=str(raw_path.resolve()),
            normalized_output_path=str(normalized_path.resolve()),
            input_fingerprint=preflight.request_fingerprint,
            status="needs_review",
            authoritative=False,
            api_called=True,
            translation_ready=False,
        )
        atomic_write_json(
            validation_path,
            {
                "schema_version": VISION_SCHEMA_VERSION,
                "valid": False,
                "errors": errors,
                "unknown_fields_silently_removed": False,
                "transcription_text_modified": False,
            },
        )
        return fallback


def _http_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _safe_error_message(exc: Exception, secret: str) -> str:
    return str(exc).replace(secret, "[REDACTED]")[:1000]


def run_vision_page(
    pdf_path: str | Path,
    pages: list[int],
    settings: ProjectSettings,
    *,
    provider: str | None = None,
    model: str | None = None,
    allow_api: bool = False,
    confirm_one_call: bool = False,
    root: Path | None = None,
    provider_factory: Callable[..., Any] = ZhipuOpenAICompatibleProvider,
) -> VisionCallResult:
    """Run zero or one real call. Repeated successful fingerprints use cache."""

    if len(pages) != 1:
        raise ValueError("Phase 2A requires exactly one PDF page")
    page = pages[0]
    root = (root or project_root()).resolve()
    preflight = vision_preflight(
        pdf_path, page, settings, provider=provider, model=model, root=root
    )
    paths = _vision_paths(
        settings,
        root,
        preflight.provider,
        preflight.model,
        page,
        preflight.request_fingerprint,
    )
    if preflight.cache_hit:
        cache = load_json(paths["cache"])
        return VisionCallResult(
            status=str(cache.get("status", "cache_hit")),
            request_fingerprint=preflight.request_fingerprint,
            pdf_page=page,
            api_called=False,
            cache_hit=True,
            real_calls_started_this_run=0,
            request_id=cache.get("request_id"),
            usage=load_json(paths["usage"]).get("usage") if paths["usage"].is_file() else None,
            raw_response_path=str(cache.get("raw_response_path")),
            normalized_output_path=str(cache.get("normalized_output_path")),
            validation_path=str(cache.get("validation_path")),
            usage_path=str(paths["usage"].resolve()) if paths["usage"].is_file() else None,
            request_metadata_path=str(paths["request"].resolve()),
        )
    if not allow_api:
        return VisionCallResult(
            status="dry_run_api_not_allowed",
            request_fingerprint=preflight.request_fingerprint,
            pdf_page=page,
            api_called=False,
            cache_hit=False,
            real_calls_started_this_run=0,
        )
    if not confirm_one_call:
        raise PermissionError("--confirm-one-call is required for the one approved request")
    if not preflight.ready_for_real_call:
        raise RuntimeError("Preflight failed: " + "; ".join(preflight.blockers))

    with _exclusive_call_lock(paths["lock"]):
        preflight = vision_preflight(
            pdf_path, page, settings, provider=provider, model=model, root=root
        )
        if preflight.cache_hit:
            return run_vision_page(
                pdf_path,
                pages,
                settings,
                provider=provider,
                model=model,
                allow_api=False,
                confirm_one_call=False,
                root=root,
                provider_factory=provider_factory,
            )
        if not preflight.ready_for_real_call:
            raise RuntimeError("Preflight failed: " + "; ".join(preflight.blockers))
        api_key, key_name = load_api_key(settings, root)
        ledger = _load_ledger(paths["ledger"], settings.vision_maximum_real_calls)
        if int(ledger.get("real_calls_started", 0)) >= settings.vision_maximum_real_calls:
            raise RuntimeError("Persistent Phase 2A real-call limit is exhausted")
        started_at = _utc_now()
        request_metadata = {
            "schema_version": "1.0",
            "phase": "2A",
            "status": "in_flight",
            "request_fingerprint": preflight.request_fingerprint,
            "provider": preflight.provider,
            "model": preflight.model,
            "base_url": preflight.base_url,
            "api_key_env": key_name,
            "api_key_recorded": False,
            "pdf_page": page,
            "input_page_count": 1,
            "source_pdf_sha256": preflight.source_pdf_sha256,
            "image_path": preflight.image_path,
            "image_sha256": preflight.image_sha256,
            "image_size_bytes": preflight.image_size_bytes,
            "image_input_mode": preflight.input_mode,
            "base64_data_url_recorded": False,
            "prompt_version": preflight.prompt_version,
            "prompt_sha256": preflight.prompt_sha256,
            "schema_version_requested": preflight.schema_version,
            "max_output_tokens": preflight.max_output_tokens,
            "temperature": preflight.temperature,
            "do_sample": preflight.do_sample,
            "thinking_mode": preflight.thinking_mode,
            "response_format_json_object": preflight.response_format_json_object,
            "automatic_retry": False,
            "translation_calls": 0,
            "started_at": started_at.isoformat(),
        }
        atomic_write_json(paths["request"], request_metadata)
        ledger["real_calls_started"] = int(ledger.get("real_calls_started", 0)) + 1
        ledger.setdefault("attempts", []).append(
            {
                "request_fingerprint": preflight.request_fingerprint,
                "pdf_page": page,
                "status": "in_flight",
                "started_at": started_at.isoformat(),
                "request_metadata_path": str(paths["request"].resolve()),
            }
        )
        atomic_write_json(paths["ledger"], ledger)

        image_bytes = Path(preflight.image_path).read_bytes()
        image_data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        prompt = Path(preflight.prompt_path).read_text(encoding="utf-8")
        context_message = (
            f"Technical context only: document_id={preflight.document_id}; "
            f"pdf_page={page}; provider={preflight.provider}; model={preflight.model}; "
            f"schema_version={VISION_SCHEMA_VERSION}. Use only the attached image."
        )
        provider_client = provider_factory(
            api_key=api_key,
            base_url=preflight.base_url,
            timeout_seconds=settings.vision_request_timeout_seconds,
        )
        call_started = time.perf_counter()
        try:
            response = provider_client.transcribe_one_page(
                model=preflight.model,
                prompt=prompt,
                context_message=context_message,
                image_data_url=image_data_url,
                max_output_tokens=preflight.max_output_tokens,
                temperature=preflight.temperature,
                do_sample=preflight.do_sample,
                thinking_mode=preflight.thinking_mode,
                response_format_json_object=preflight.response_format_json_object,
            )
            elapsed = round(time.perf_counter() - call_started, 3)
            atomic_write_json(
                paths["raw"],
                {
                    "record_type": "raw_provider_response",
                    "request_fingerprint": preflight.request_fingerprint,
                    "provider": preflight.provider,
                    "model": preflight.model,
                    "api_called": True,
                    "received_at": _utc_now().isoformat(),
                    "response": response.raw_response,
                },
            )
            normalized = _normalize_response(
                content=response.content,
                preflight=preflight,
                raw_path=paths["raw"],
                normalized_path=paths["normalized"],
                validation_path=paths["validation"],
            )
            atomic_write_json(paths["normalized"], normalized)
            atomic_write_json(
                paths["usage"],
                {
                    "request_fingerprint": preflight.request_fingerprint,
                    "request_id": response.request_id,
                    "usage": response.usage,
                    "api_returned_account_balance": False,
                    "account_balance": None,
                    "cash_charge_confirmed": False,
                    "cash_charge_cny": None,
                    "note": "API response does not establish token-package coverage or cash billing.",
                },
            )
            request_metadata.update(
                {
                    "status": "completed",
                    "request_id": response.request_id,
                    "ended_at": _utc_now().isoformat(),
                    "elapsed_seconds": elapsed,
                    "raw_response_path": str(paths["raw"].resolve()),
                    "normalized_output_path": str(paths["normalized"].resolve()),
                    "usage_path": str(paths["usage"].resolve()),
                    "retries": 0,
                }
            )
            atomic_write_json(paths["request"], request_metadata)
            ledger["attempts"][-1].update(
                {
                    "status": "completed",
                    "ended_at": _utc_now().isoformat(),
                    "request_id": response.request_id,
                }
            )
            atomic_write_json(paths["ledger"], ledger)
            cache = {
                "request_fingerprint": preflight.request_fingerprint,
                "api_call_completed": True,
                "status": normalized.status,
                "request_id": response.request_id,
                "raw_response_path": str(paths["raw"].resolve()),
                "normalized_output_path": str(paths["normalized"].resolve()),
                "validation_path": str(paths["validation"].resolve()),
                "usage_path": str(paths["usage"].resolve()),
                "authoritative": False,
                "translation_ready": False,
            }
            atomic_write_json(paths["cache"], cache)
            return VisionCallResult(
                status=normalized.status,
                request_fingerprint=preflight.request_fingerprint,
                pdf_page=page,
                api_called=True,
                cache_hit=False,
                real_calls_started_this_run=1,
                request_id=response.request_id,
                usage=response.usage,
                raw_response_path=str(paths["raw"].resolve()),
                normalized_output_path=str(paths["normalized"].resolve()),
                validation_path=str(paths["validation"].resolve()),
                usage_path=str(paths["usage"].resolve()),
                request_metadata_path=str(paths["request"].resolve()),
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = round(time.perf_counter() - call_started, 3)
            error_type = type(exc).__name__
            status_code = _http_status(exc)
            safe_message = _safe_error_message(exc, api_key)
            # If the provider response was already preserved, a later local
            # normalization/write failure must never overwrite that raw record.
            error_record_path = paths["error"] if paths["raw"].is_file() else paths["raw"]
            atomic_write_json(
                error_record_path,
                {
                    "record_type": "provider_error_response",
                    "request_fingerprint": preflight.request_fingerprint,
                    "provider": preflight.provider,
                    "model": preflight.model,
                    "api_called": True,
                    "error_type": error_type,
                    "http_status": status_code,
                    "error_message": safe_message,
                    "usage": None,
                    "automatic_retry": False,
                    "retries": 0,
                    "recorded_at": _utc_now().isoformat(),
                },
            )
            request_metadata.update(
                {
                    "status": "failed",
                    "ended_at": _utc_now().isoformat(),
                    "elapsed_seconds": elapsed,
                    "error_type": error_type,
                    "http_status": status_code,
                    "raw_response_path": str(paths["raw"].resolve()),
                    "error_record_path": str(error_record_path.resolve()),
                    "retries": 0,
                }
            )
            atomic_write_json(paths["request"], request_metadata)
            ledger["attempts"][-1].update(
                {
                    "status": "failed",
                    "ended_at": _utc_now().isoformat(),
                    "error_type": error_type,
                    "http_status": status_code,
                }
            )
            atomic_write_json(paths["ledger"], ledger)
            raise VisionCallFailed(
                "The single approved request failed and was not retried",
                error_type=error_type,
                http_status=status_code,
                raw_response_path=str(paths["raw"].resolve()),
                usage_available=False,
            ) from exc
