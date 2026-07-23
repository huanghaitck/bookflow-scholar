"""Phase 2B preflight and capped single/pair/triple visual calls."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from pydantic import BaseModel, Field, ValidationError

from .io_utils import atomic_write_json, load_json, sha256_file, sha256_text, stable_hash
from .page_pipeline import build_context, page_status
from .paths import ProjectSettings, project_root, resolve_project_path
from .phase2a1 import NormalizationEvent, VisionNormalizedPageV11, normalize_preserved_response_v11
from .phase2b_schemas import BoundaryDecision, BoundaryModelPayload
from .schemas import PageRecord
from .secret_store import api_key_status, load_api_key
from .vision_provider import ProviderResponse, ZhipuOpenAICompatibleProvider


class Phase2BPreflight(BaseModel):
    sample_pdf: str
    actual_page_count: int
    document_id: str
    sample_pdf_sha256: str
    page_images_ready: int
    page6_phase2a_cache_reused: bool
    single_calls_expected: int
    pair_calls_expected: int
    triple_calls_maximum: int
    maximum_new_calls: int
    calls_already_started: int
    remaining_total_calls: int
    automatic_retry: bool
    deepseek_calls: int = 0
    translation_calls: int = 0
    full_pdf_processing: bool = False
    estimated_token_range: str
    estimated_public_price_cny: float
    estimated_public_price_range_cny: str
    maximum_estimated_cash_cost_cny: float
    api_returns_balance_or_cash_charge: bool = False
    api_key_env: str
    api_key_set: bool
    blockers: list[str] = Field(default_factory=list)
    ready_for_real_calls: bool
    offline: bool = True
    api_called: bool = False


class BatchCallResult(BaseModel):
    category: str
    requested: int
    api_calls_started: int
    cache_hits: int
    phase2a_cache_hits: int = 0
    completed: int
    needs_review: int
    failed: int
    failed_items: list[str] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.") or "value"


def _sample_context(pdf_path: str | Path, settings: ProjectSettings, root: Path):
    source = resolve_project_path(pdf_path, root=root)
    sample = resolve_project_path(settings.sample_pdf, root=root)
    protected = resolve_project_path(settings.source_pdf, root=root)
    if source == protected or source != sample:
        raise PermissionError("Phase 2B only accepts the configured 11-page sample")
    context = build_context(
        source,
        settings,
        pages=list(range(1, 12)),
        dpi=settings.render_dpi,
        color_mode=settings.render_color_mode,
        image_format=settings.render_format,
        root=root,
    )
    if context.page_count != 11:
        raise ValueError(f"Phase 2B requires the actual 11-page sample, found {context.page_count}")
    return source, context


def _record(context, page: int) -> PageRecord:
    path = Path(context.record_directory) / f"page_{page:04d}.json"
    return PageRecord.model_validate(load_json(path))


def _page6_v11(settings: ProjectSettings, root: Path) -> Path | None:
    directory = (
        resolve_project_path(settings.vision_normalized_v11_directory, root=root)
        / _safe(settings.vision_provider)
        / _safe(settings.vision_model)
        / "page_0006"
    )
    matches = sorted(directory.glob("*.json")) if directory.is_dir() else []
    for path in reversed(matches):
        try:
            result = VisionNormalizedPageV11.model_validate(load_json(path))
        except Exception:
            continue
        if result.pdf_page == 6 and result.api_called:
            return path
    return None


def _latest_page_result(settings: ProjectSettings, root: Path, page: int) -> Path | None:
    directory = (
        resolve_project_path(settings.vision_normalized_v11_directory, root=root)
        / _safe(settings.vision_provider)
        / _safe(settings.vision_model)
        / f"page_{page:04d}"
    )
    valid: list[tuple[datetime, Path]] = []
    for path in directory.glob("*.json") if directory.is_dir() else []:
        try:
            result = VisionNormalizedPageV11.model_validate(load_json(path))
            valid.append((result.normalized_at, path))
        except Exception:
            continue
    return max(valid, default=(None, None), key=lambda item: item[0])[1]


def load_all_page_results(settings: ProjectSettings, root: Path) -> dict[int, VisionNormalizedPageV11]:
    results: dict[int, VisionNormalizedPageV11] = {}
    for page in range(1, 12):
        path = _latest_page_result(settings, root, page)
        if path:
            results[page] = VisionNormalizedPageV11.model_validate(load_json(path))
    return results


def _ledger_path(settings: ProjectSettings, root: Path) -> Path:
    return resolve_project_path(settings.phase2b_request_directory, root=root) / "phase2b_call_ledger.json"


def _lock_path(settings: ProjectSettings, root: Path) -> Path:
    return resolve_project_path(settings.phase2b_request_directory, root=root) / "phase2b_call_ledger.lock"


def _load_ledger(settings: ProjectSettings, root: Path) -> dict[str, Any]:
    path = _ledger_path(settings, root)
    if not path.is_file():
        return {
            "schema_version": "1.0",
            "phase": "2B",
            "limits": {
                "single": settings.phase2b_max_single_calls,
                "pair": settings.phase2b_max_pair_calls,
                "triple": settings.phase2b_max_triple_calls,
                "total": settings.phase2b_max_total_calls,
            },
            "started": {"single": 0, "pair": 0, "triple": 0, "total": 0},
            "attempts": [],
        }
    value = load_json(path)
    if not isinstance(value, dict):
        raise RuntimeError("Phase 2B call ledger is invalid")
    return value


@contextmanager
def _call_lock(settings: ProjectSettings, root: Path) -> Iterator[None]:
    path = _lock_path(settings, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Another Phase 2B call may be in progress") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        if path.exists():
            path.unlink()


def _reserve_call(
    settings: ProjectSettings,
    root: Path,
    category: Literal["single", "pair", "triple"],
    item_id: str,
    fingerprint: str,
) -> tuple[dict[str, Any], int]:
    ledger = _load_ledger(settings, root)
    limits = ledger["limits"]
    started = ledger["started"]
    if started[category] >= limits[category] or started["total"] >= limits["total"]:
        raise RuntimeError(f"Persistent Phase 2B {category} or total call limit is exhausted")
    started[category] += 1
    started["total"] += 1
    attempt = {
        "attempt_number": len(ledger["attempts"]) + 1,
        "category": category,
        "item_id": item_id,
        "request_fingerprint": fingerprint,
        "status": "in_flight",
        "started_at": _now().isoformat(),
        "automatic_retry": False,
        "retries": 0,
    }
    ledger["attempts"].append(attempt)
    atomic_write_json(_ledger_path(settings, root), ledger)
    return ledger, len(ledger["attempts"]) - 1


def _finish_call(
    settings: ProjectSettings,
    root: Path,
    ledger: dict[str, Any],
    index: int,
    **values: Any,
) -> None:
    ledger["attempts"][index].update(values)
    ledger["attempts"][index]["ended_at"] = _now().isoformat()
    atomic_write_json(_ledger_path(settings, root), ledger)


def _image_data_url(path: str | Path) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _provider(
    settings: ProjectSettings,
    root: Path,
    provider_factory: Callable[..., Any],
):
    secret, key_name = load_api_key(settings, root)
    return (
        provider_factory(
            api_key=secret,
            base_url=settings.vision_base_url,
            timeout_seconds=settings.vision_request_timeout_seconds,
        ),
        secret,
        key_name,
    )


def _safe_error(exc: Exception, secret: str) -> str:
    return str(exc).replace(secret, "[REDACTED]")[:1000]


def _http_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _call_provider_once(
    *,
    settings: ProjectSettings,
    root: Path,
    category: Literal["single", "pair", "triple"],
    item_id: str,
    fingerprint: str,
    prompt: str,
    context_message: str,
    image_paths: list[Path],
    raw_path: Path,
    request_path: Path,
    usage_path: Path,
    provider_factory: Callable[..., Any],
) -> tuple[ProviderResponse, float]:
    with _call_lock(settings, root):
        client, secret, key_name = _provider(settings, root, provider_factory)
        ledger, index = _reserve_call(settings, root, category, item_id, fingerprint)
        metadata = {
            "schema_version": "1.0",
            "phase": "2B",
            "category": category,
            "item_id": item_id,
            "status": "in_flight",
            "request_fingerprint": fingerprint,
            "provider": settings.vision_provider,
            "model": settings.vision_model,
            "base_url": settings.vision_base_url,
            "api_key_env": key_name,
            "api_key_recorded": False,
            "image_paths": [str(path.resolve()) for path in image_paths],
            "image_sha256": [sha256_file(path) for path in image_paths],
            "image_count": len(image_paths),
            "base64_data_url_recorded": False,
            "automatic_retry": False,
            "retries": 0,
            "translation_calls": 0,
            "started_at": _now().isoformat(),
        }
        atomic_write_json(request_path, metadata)
        started = time.perf_counter()
        try:
            response = client.transcribe_images(
                model=settings.vision_model,
                prompt=prompt,
                context_message=context_message,
                image_data_urls=[_image_data_url(path) for path in image_paths],
                max_output_tokens=settings.vision_max_output_tokens,
                temperature=settings.vision_temperature,
                do_sample=settings.vision_do_sample,
                thinking_mode=settings.vision_thinking_mode,
                response_format_json_object=settings.vision_response_format_json_object,
            )
        except Exception as exc:
            elapsed = round(time.perf_counter() - started, 3)
            atomic_write_json(
                raw_path,
                {
                    "record_type": "provider_error_response",
                    "phase": "2B",
                    "category": category,
                    "item_id": item_id,
                    "request_fingerprint": fingerprint,
                    "provider": settings.vision_provider,
                    "model": settings.vision_model,
                    "api_called": True,
                    "error_type": type(exc).__name__,
                    "http_status": _http_status(exc),
                    "error_message": _safe_error(exc, secret),
                    "usage": None,
                    "automatic_retry": False,
                    "retries": 0,
                    "recorded_at": _now().isoformat(),
                },
            )
            metadata.update(
                {
                    "status": "failed",
                    "ended_at": _now().isoformat(),
                    "elapsed_seconds": elapsed,
                    "error_type": type(exc).__name__,
                    "http_status": _http_status(exc),
                    "raw_response_path": str(raw_path.resolve()),
                }
            )
            atomic_write_json(request_path, metadata)
            _finish_call(
                settings,
                root,
                ledger,
                index,
                status="failed",
                error_type=type(exc).__name__,
                http_status=_http_status(exc),
            )
            raise
        elapsed = round(time.perf_counter() - started, 3)
        atomic_write_json(
            raw_path,
            {
                "record_type": "raw_provider_response",
                "phase": "2B",
                "category": category,
                "item_id": item_id,
                "request_fingerprint": fingerprint,
                "provider": settings.vision_provider,
                "model": settings.vision_model,
                "api_called": True,
                "received_at": _now().isoformat(),
                "response": response.raw_response,
            },
        )
        atomic_write_json(
            usage_path,
            {
                "phase": "2B",
                "category": category,
                "item_id": item_id,
                "request_fingerprint": fingerprint,
                "request_id": response.request_id,
                "usage": response.usage,
                "api_returned_account_balance": False,
                "account_balance": None,
                "cash_charge_confirmed": False,
                "cash_charge_cny": None,
            },
        )
        metadata.update(
            {
                "status": "response_saved",
                "request_id": response.request_id,
                "raw_response_path": str(raw_path.resolve()),
                "usage_path": str(usage_path.resolve()),
                "ended_at": _now().isoformat(),
                "elapsed_seconds": elapsed,
            }
        )
        atomic_write_json(request_path, metadata)
        _finish_call(
            settings,
            root,
            ledger,
            index,
            status="response_saved",
            request_id=response.request_id,
            usage_path=str(usage_path.resolve()),
        )
        return response, elapsed


def phase2b_preflight(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    root: Path | None = None,
) -> Phase2BPreflight:
    root = (root or project_root()).resolve()
    source, context = _sample_context(pdf_path, settings, root)
    status = page_status(
        source,
        settings,
        pages="1-11",
        dpi=settings.render_dpi,
        color_mode=settings.render_color_mode,
        image_format=settings.render_format,
        root=root,
    )
    results = load_all_page_results(settings, root)
    page6 = _page6_v11(settings, root)
    page_prompt = resolve_project_path(settings.phase2b_page_prompt_path, root=root)
    boundary_prompt = resolve_project_path(settings.boundary_prompt_path, root=root)
    boundary_cache_root = resolve_project_path(settings.boundary_cache_directory, root=root)
    pair_hits = sum(
        1
        for page in range(1, 11)
        if any((boundary_cache_root / "pair" / f"p{page:04d}_p{page+1:04d}").glob("*.json"))
    )
    single_expected = len([page for page in range(1, 12) if page != 6 and page not in results])
    pair_expected = 10 - pair_hits
    ledger = _load_ledger(settings, root)
    started = int(ledger["started"]["total"])
    key_set, _ = api_key_status(settings, root)
    blockers: list[str] = []
    if not status.ready_for_vision or len(status.rendered_pages) != 11:
        blockers.append("The 11 rendered pages or manifest are not complete")
    if page6 is None:
        blockers.append("The Phase 2A page 6 cache has not been patched to Schema 1.1")
    if not page_prompt.is_file() or not boundary_prompt.is_file():
        blockers.append("A versioned Phase 2B prompt is missing")
    if settings.phase2b_automatic_retry or settings.vision_automatic_retry:
        blockers.append("Automatic retry must remain disabled")
    if settings.phase2b_translation_enabled or settings.translation_enabled:
        blockers.append("Translation and DeepSeek must remain disabled")
    if settings.automatic_phase_advance:
        blockers.append("Automatic phase advance must remain disabled")
    if settings.phase2b_max_total_calls > 23:
        blockers.append("Phase 2B total call limit exceeds 23")
    if not key_set and (single_expected + pair_expected) > 0:
        blockers.append(f"API key is not configured in {settings.vision_api_key_env}")
    estimated_input_tokens = 96_060
    estimated_output_tokens = 13_450
    estimated_cost = round(
        (
            estimated_input_tokens * settings.vision_input_price_cny_per_million_tokens
            + estimated_output_tokens * settings.vision_output_price_cny_per_million_tokens
        )
        / 1_000_000,
        6,
    )
    if estimated_cost > settings.phase2b_maximum_estimated_cash_cost_cny:
        blockers.append("Estimated public-price cost exceeds the configured Phase 2B ceiling")
    return Phase2BPreflight(
        sample_pdf=str(source),
        actual_page_count=context.page_count,
        document_id=context.document_id,
        sample_pdf_sha256=context.source_pdf_sha256,
        page_images_ready=len(status.rendered_pages),
        page6_phase2a_cache_reused=page6 is not None,
        single_calls_expected=single_expected,
        pair_calls_expected=pair_expected,
        triple_calls_maximum=settings.phase2b_max_triple_calls,
        maximum_new_calls=settings.phase2b_max_total_calls,
        calls_already_started=started,
        remaining_total_calls=max(0, settings.phase2b_max_total_calls - started),
        automatic_retry=settings.phase2b_automatic_retry,
        estimated_token_range="approximately 80,000-140,000 total tokens",
        estimated_public_price_cny=estimated_cost,
        estimated_public_price_range_cny="approximately CNY 0.10-0.25",
        maximum_estimated_cash_cost_cny=settings.phase2b_maximum_estimated_cash_cost_cny,
        api_key_env=settings.vision_api_key_env,
        api_key_set=key_set,
        blockers=blockers,
        ready_for_real_calls=not blockers,
    )


def _page_paths(
    settings: ProjectSettings, root: Path, page: int, fingerprint: str
) -> dict[str, Path]:
    provider = _safe(settings.vision_provider)
    model = _safe(settings.vision_model)
    page_part = f"page_{page:04d}"
    return {
        "raw": resolve_project_path(settings.vision_raw_directory, root=root)
        / provider / model / page_part / f"{fingerprint}.json",
        "normalized": resolve_project_path(settings.vision_normalized_v11_directory, root=root)
        / provider / model / page_part / f"{fingerprint}.json",
        "cache": resolve_project_path(settings.phase2b_page_cache_directory, root=root)
        / page_part / f"{fingerprint}.json",
        "request": resolve_project_path(settings.phase2b_request_directory, root=root)
        / "single" / f"{fingerprint}.json",
        "usage": resolve_project_path(settings.phase2b_usage_directory, root=root)
        / "single" / f"{fingerprint}.json",
    }


def run_single_pages(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    pages: list[int] | None = None,
    allow_api: bool = False,
    confirm_phase2b: bool = False,
    root: Path | None = None,
    provider_factory: Callable[..., Any] = ZhipuOpenAICompatibleProvider,
) -> BatchCallResult:
    root = (root or project_root()).resolve()
    source, context = _sample_context(pdf_path, settings, root)
    selected = pages or list(range(1, 12))
    if any(page < 1 or page > 11 for page in selected) or len(set(selected)) != len(selected):
        raise ValueError("Phase 2B pages must be unique values from 1 through 11")
    prompt_path = resolve_project_path(settings.phase2b_page_prompt_path, root=root)
    prompt = prompt_path.read_text(encoding="utf-8")
    prompt_hash = sha256_text(prompt)
    api_calls = cache_hits = phase2a_hits = completed = needs_review = failed = 0
    failures: list[str] = []
    for page in selected:
        if page == 6:
            if _page6_v11(settings, root) is None:
                failed += 1
                failures.append("page_0006_missing_phase2a_cache")
            else:
                phase2a_hits += 1
                completed += 1
            continue
        record = _record(context, page)
        fingerprint = stable_hash(
            {
                "phase": "2B",
                "category": "single",
                "provider": settings.vision_provider,
                "model": settings.vision_model,
                "base_url": settings.vision_base_url.rstrip("/"),
                "document_id": context.document_id,
                "pdf_page": page,
                "image_sha256": record.image_sha256,
                "prompt_version": settings.phase2b_page_prompt_version,
                "prompt_sha256": prompt_hash,
                "normalized_schema_version": settings.vision_normalized_schema_version,
                "max_output_tokens": settings.vision_max_output_tokens,
                "do_sample": settings.vision_do_sample,
                "thinking_mode": settings.vision_thinking_mode,
            }
        )
        paths = _page_paths(settings, root, page, fingerprint)
        if paths["cache"].is_file() and paths["raw"].is_file() and paths["normalized"].is_file():
            cache_hits += 1
            completed += 1
            continue
        if paths["raw"].is_file() and not paths["normalized"].is_file():
            try:
                normalized = normalize_preserved_response_v11(
                    paths["raw"], paths["normalized"], force_adjacent_review=set()
                )
                raw_record = load_json(paths["raw"])
                atomic_write_json(
                    paths["cache"],
                    {
                        "request_fingerprint": fingerprint,
                        "api_call_completed": True,
                        "offline_normalization_recovery": True,
                        "raw_response_path": str(paths["raw"].resolve()),
                        "normalized_output_path": str(paths["normalized"].resolve()),
                        "request_id": raw_record.get("response", {}).get("id"),
                        "status": normalized.status,
                        "authoritative": False,
                        "translation_ready": False,
                    },
                )
                cache_hits += 1
                completed += 1
                if normalized.status == "needs_review":
                    needs_review += 1
            except Exception as exc:
                failed += 1
                failures.append(f"page_{page:04d}:offline_{type(exc).__name__}")
            continue
        if not allow_api:
            continue
        if not confirm_phase2b:
            raise PermissionError("--confirm-phase2b is required for approved real calls")
        try:
            api_calls += 1
            response, _ = _call_provider_once(
                settings=settings,
                root=root,
                category="single",
                item_id=f"page_{page:04d}",
                fingerprint=fingerprint,
                prompt=prompt,
                context_message=(
                    f"document_id={context.document_id}; pdf_page={page}; "
                    f"provider={settings.vision_provider}; model={settings.vision_model}; "
                    "normalized_schema_version=1.1. Use only this one attached page image."
                ),
                image_paths=[Path(record.image_path)],
                raw_path=paths["raw"],
                request_path=paths["request"],
                usage_path=paths["usage"],
                provider_factory=provider_factory,
            )
            normalized = normalize_preserved_response_v11(
                paths["raw"], paths["normalized"], force_adjacent_review=set()
            )
            atomic_write_json(
                paths["cache"],
                {
                    "request_fingerprint": fingerprint,
                    "api_call_completed": True,
                    "raw_response_path": str(paths["raw"].resolve()),
                    "normalized_output_path": str(paths["normalized"].resolve()),
                    "request_id": response.request_id,
                    "status": normalized.status,
                    "authoritative": False,
                    "translation_ready": False,
                },
            )
            completed += 1
            if normalized.status == "needs_review":
                needs_review += 1
        except Exception as exc:
            failed += 1
            failures.append(f"page_{page:04d}:{type(exc).__name__}")
    return BatchCallResult(
        category="single",
        requested=len(selected),
        api_calls_started=api_calls,
        cache_hits=cache_hits,
        phase2a_cache_hits=phase2a_hits,
        completed=completed,
        needs_review=needs_review,
        failed=failed,
        failed_items=failures,
    )


def _body_edges(result: VisionNormalizedPageV11) -> tuple[str | None, str, str | None, str]:
    body = [block for block in result.blocks if block.block_type == "body"]
    if not body:
        return None, "", None, ""
    first, last = body[0], body[-1]
    return first.block_id, first.text[:500], last.block_id, last.text[-500:]


def _boundary_paths(
    settings: ProjectSettings,
    root: Path,
    category: Literal["pair", "triple"],
    previous: int,
    next_page: int,
    fingerprint: str,
) -> dict[str, Path]:
    item = f"p{previous:04d}_p{next_page:04d}"
    return {
        "raw": resolve_project_path(settings.boundary_raw_directory, root=root)
        / category / item / f"{fingerprint}.json",
        "normalized": resolve_project_path(settings.boundary_normalized_directory, root=root)
        / category / item / f"{fingerprint}.json",
        "validation": resolve_project_path(settings.boundary_normalized_directory, root=root)
        / category / item / f"{fingerprint}.validation.json",
        "cache": resolve_project_path(settings.boundary_cache_directory, root=root)
        / category / item / f"{fingerprint}.json",
        "request": resolve_project_path(settings.phase2b_request_directory, root=root)
        / category / f"{fingerprint}.json",
        "usage": resolve_project_path(settings.phase2b_usage_directory, root=root)
        / category / f"{fingerprint}.json",
    }


def _boundary_from_response(
    response: ProviderResponse,
    *,
    settings: ProjectSettings,
    context,
    previous_record: PageRecord,
    next_record: PageRecord,
    previous_result: VisionNormalizedPageV11,
    next_result: VisionNormalizedPageV11,
    review_window: list[int],
    raw_path: Path,
    normalized_path: Path,
    validation_path: Path,
) -> BoundaryDecision:
    boundary_id = f"boundary_p{previous_record.pdf_page:04d}_p{next_record.pdf_page:04d}"
    next_first, next_head, previous_last, previous_tail = (None, "", None, "")
    next_first, next_head, _, _ = _body_edges(next_result)
    _, _, previous_last, previous_tail = _body_edges(previous_result)
    try:
        content = response.content.strip()
        if content.startswith("```"):
            lines = content.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines.pop()
            content = "\n".join(lines).strip()
        raw_payload = json.loads(content)
        events: list[NormalizationEvent] = []
        if isinstance(raw_payload, dict) and isinstance(raw_payload.get("evidence"), str):
            evidence = raw_payload["evidence"]
            raw_payload = dict(raw_payload)
            raw_payload["evidence"] = [evidence]
            events.append(
                NormalizationEvent(
                    field="evidence",
                    action="nonempty_string_to_single_item_list",
                    reason="A single evidence statement can be preserved losslessly as a one-item array.",
                    original_type="string",
                    original_value=evidence,
                    normalized_value=[evidence],
                )
            )
        payload = BoundaryModelPayload.model_validate(raw_payload)
        identity_ok = (
            payload.boundary_id == boundary_id
            and payload.document_id == context.document_id
            and payload.previous_page == previous_record.pdf_page
            and payload.next_page == next_record.pdf_page
        )
        if not identity_ok:
            raise ValueError("Boundary response identity does not match the request")
        uncertainty = (
            payload.word_continuation is None
            or payload.sentence_continuation is None
            or payload.paragraph_continuation is None
            or payload.join_operation == "uncertain"
            or payload.structural_break == "unknown"
            or payload.hyphen_type == "uncertain"
        )
        human_required = payload.needs_human_review or uncertainty
        decision = BoundaryDecision(
            schema_version=settings.boundary_schema_version,
            boundary_id=boundary_id,
            document_id=context.document_id,
            previous_page=previous_record.pdf_page,
            next_page=next_record.pdf_page,
            previous_image_sha256=previous_record.image_sha256,
            next_image_sha256=next_record.image_sha256,
            previous_last_block_id=payload.previous_last_block_id or previous_last,
            next_first_block_id=payload.next_first_block_id or next_first,
            previous_tail_text=previous_tail,
            next_head_text=next_head,
            word_continuation=payload.word_continuation,
            sentence_continuation=payload.sentence_continuation,
            paragraph_continuation=payload.paragraph_continuation,
            structural_break=payload.structural_break,
            join_operation=payload.join_operation,
            hyphen_type=payload.hyphen_type,
            header_footer_interference=payload.header_footer_interference,
            reconstructed_boundary_text=payload.reconstructed_boundary_text,
            evidence=payload.evidence,
            confidence=payload.confidence,
            provider=settings.vision_provider,
            model=settings.vision_model,
            review_window=review_window,
            raw_response_path=str(raw_path.resolve()),
            normalization_events=events,
            model_review_status="needs_review" if uncertainty else "completed",
            human_review_status="required" if human_required else "not_required",
            needs_triple_review=payload.needs_triple_review or uncertainty,
            status="needs_review" if human_required else payload.status,
        )
        atomic_write_json(
            validation_path,
            {"valid": True, "errors": [], "schema_version": settings.boundary_schema_version},
        )
    except Exception as exc:
        errors = (
            exc.errors(include_input=False, include_url=False)
            if isinstance(exc, ValidationError)
            else [{"type": type(exc).__name__, "msg": str(exc)[:500]}]
        )
        decision = BoundaryDecision(
            schema_version=settings.boundary_schema_version,
            boundary_id=boundary_id,
            document_id=context.document_id,
            previous_page=previous_record.pdf_page,
            next_page=next_record.pdf_page,
            previous_image_sha256=previous_record.image_sha256,
            next_image_sha256=next_record.image_sha256,
            previous_last_block_id=previous_last,
            next_first_block_id=next_first,
            previous_tail_text=previous_tail,
            next_head_text=next_head,
            word_continuation=None,
            sentence_continuation=None,
            paragraph_continuation=None,
            structural_break="unknown",
            join_operation="uncertain",
            hyphen_type="uncertain",
            header_footer_interference=None,
            reconstructed_boundary_text="",
            evidence=["Boundary response failed local Schema validation."],
            confidence=None,
            provider=settings.vision_provider,
            model=settings.vision_model,
            review_window=review_window,
            raw_response_path=str(raw_path.resolve()),
            normalization_events=[],
            model_review_status="schema_failed",
            human_review_status="required",
            needs_triple_review=True,
            status="needs_review",
        )
        atomic_write_json(
            validation_path,
            {"valid": False, "errors": errors, "schema_version": settings.boundary_schema_version},
        )
    atomic_write_json(normalized_path, decision)
    return decision


def _saved_provider_response(raw_path: Path) -> ProviderResponse:
    raw = load_json(raw_path)
    response = raw.get("response")
    if not isinstance(response, dict):
        raise ValueError("Saved boundary raw response is unavailable")
    content = response.get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("Saved boundary response has no content string")
    return ProviderResponse(
        raw_response=response,
        content=content,
        request_id=str(response.get("id")) if response.get("id") is not None else None,
        usage=response.get("usage") if isinstance(response.get("usage"), dict) else {},
        response_model=str(response.get("model")) if response.get("model") is not None else None,
    )


def run_pair_boundaries(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    boundaries: list[tuple[int, int]] | None = None,
    allow_api: bool = False,
    confirm_phase2b: bool = False,
    root: Path | None = None,
    provider_factory: Callable[..., Any] = ZhipuOpenAICompatibleProvider,
) -> BatchCallResult:
    root = (root or project_root()).resolve()
    _, context = _sample_context(pdf_path, settings, root)
    pairs = boundaries or [(page, page + 1) for page in range(1, 11)]
    if any(next_page != previous + 1 or previous < 1 or next_page > 11 for previous, next_page in pairs):
        raise ValueError("Pair review only accepts the ten adjacent sample boundaries")
    page_results = load_all_page_results(settings, root)
    if len(page_results) != 11:
        raise RuntimeError("All eleven single-page normalized results are required before pair review")
    prompt_path = resolve_project_path(settings.boundary_prompt_path, root=root)
    prompt = prompt_path.read_text(encoding="utf-8")
    prompt_hash = sha256_text(prompt)
    api_calls = cache_hits = completed = needs_review = failed = 0
    failures: list[str] = []
    for previous, next_page in pairs:
        previous_result, next_result = page_results[previous], page_results[next_page]
        previous_record, next_record = _record(context, previous), _record(context, next_page)
        next_first, next_head, _, _ = _body_edges(next_result)
        _, _, previous_last, previous_tail = _body_edges(previous_result)
        boundary_id = f"boundary_p{previous:04d}_p{next_page:04d}"
        fingerprint = stable_hash(
            {
                "phase": "2B",
                "category": "pair",
                "boundary_id": boundary_id,
                "provider": settings.vision_provider,
                "model": settings.vision_model,
                "base_url": settings.vision_base_url.rstrip("/"),
                "previous_image_sha256": previous_record.image_sha256,
                "next_image_sha256": next_record.image_sha256,
                "previous_result_sha256": sha256_file(_latest_page_result(settings, root, previous)),
                "next_result_sha256": sha256_file(_latest_page_result(settings, root, next_page)),
                "prompt_version": settings.boundary_prompt_version,
                "prompt_sha256": prompt_hash,
                "boundary_schema_version": settings.boundary_schema_version,
                "max_output_tokens": settings.vision_max_output_tokens,
            }
        )
        paths = _boundary_paths(settings, root, "pair", previous, next_page, fingerprint)
        validation_record = load_json(paths["validation"]) if paths["validation"].is_file() else None
        if (
            paths["raw"].is_file()
            and isinstance(validation_record, dict)
            and validation_record.get("valid") is False
        ):
            try:
                recovered_path = paths["normalized"].with_name(f"{fingerprint}.recovered.json")
                recovered_validation = paths["validation"].with_name(
                    f"{fingerprint}.recovered.validation.json"
                )
                decision = _boundary_from_response(
                    _saved_provider_response(paths["raw"]),
                    settings=settings,
                    context=context,
                    previous_record=previous_record,
                    next_record=next_record,
                    previous_result=previous_result,
                    next_result=next_result,
                    review_window=[previous, next_page],
                    raw_path=paths["raw"],
                    normalized_path=recovered_path,
                    validation_path=recovered_validation,
                )
                cache_record = load_json(paths["cache"]) if paths["cache"].is_file() else {}
                cache_record.update(
                    {
                        "offline_normalization_recovery": True,
                        "original_normalized_output_preserved": str(paths["normalized"].resolve()),
                        "normalized_output_path": str(recovered_path.resolve()),
                        "status": decision.status,
                    }
                )
                atomic_write_json(paths["cache"], cache_record)
                cache_hits += 1
                completed += 1
                if decision.status == "needs_review":
                    needs_review += 1
            except Exception as exc:
                failed += 1
                failures.append(f"{boundary_id}:offline_{type(exc).__name__}")
            continue
        if paths["cache"].is_file() and paths["raw"].is_file() and paths["normalized"].is_file():
            cache_hits += 1
            completed += 1
            cache_record = load_json(paths["cache"])
            cached_output = Path(cache_record.get("normalized_output_path", paths["normalized"]))
            decision = BoundaryDecision.model_validate(load_json(cached_output))
            if decision.status == "needs_review":
                needs_review += 1
            continue
        if not allow_api:
            continue
        if not confirm_phase2b:
            raise PermissionError("--confirm-phase2b is required for approved pair calls")
        structured_context = {
            "boundary_id": boundary_id,
            "document_id": context.document_id,
            "previous_page": previous,
            "next_page": next_page,
            "previous_last_block_id": previous_last,
            "next_first_block_id": next_first,
            "previous_tail_text": previous_tail,
            "next_head_text": next_head,
            "previous_page_transcription": previous_result.model_dump(mode="json"),
            "next_page_transcription": next_result.model_dump(mode="json"),
        }
        try:
            api_calls += 1
            response, _ = _call_provider_once(
                settings=settings,
                root=root,
                category="pair",
                item_id=boundary_id,
                fingerprint=fingerprint,
                prompt=prompt,
                context_message=json.dumps(structured_context, ensure_ascii=False),
                image_paths=[Path(previous_record.image_path), Path(next_record.image_path)],
                raw_path=paths["raw"],
                request_path=paths["request"],
                usage_path=paths["usage"],
                provider_factory=provider_factory,
            )
            decision = _boundary_from_response(
                response,
                settings=settings,
                context=context,
                previous_record=previous_record,
                next_record=next_record,
                previous_result=previous_result,
                next_result=next_result,
                review_window=[previous, next_page],
                raw_path=paths["raw"],
                normalized_path=paths["normalized"],
                validation_path=paths["validation"],
            )
            atomic_write_json(
                paths["cache"],
                {
                    "request_fingerprint": fingerprint,
                    "api_call_completed": True,
                    "raw_response_path": str(paths["raw"].resolve()),
                    "normalized_output_path": str(paths["normalized"].resolve()),
                    "request_id": response.request_id,
                    "status": decision.status,
                },
            )
            completed += 1
            if decision.status == "needs_review":
                needs_review += 1
        except Exception as exc:
            failed += 1
            failures.append(f"{boundary_id}:{type(exc).__name__}")
    return BatchCallResult(
        category="pair",
        requested=len(pairs),
        api_calls_started=api_calls,
        cache_hits=cache_hits,
        completed=completed,
        needs_review=needs_review,
        failed=failed,
        failed_items=failures,
    )


def load_latest_boundaries(
    settings: ProjectSettings, root: Path, category: str = "pair"
) -> dict[str, BoundaryDecision]:
    base = resolve_project_path(settings.boundary_normalized_directory, root=root) / category
    results: dict[str, BoundaryDecision] = {}
    if not base.is_dir():
        return results
    for item_dir in base.iterdir():
        if not item_dir.is_dir():
            continue
        candidates = sorted(path for path in item_dir.glob("*.json") if not path.name.endswith(".validation.json"))
        for path in reversed(candidates):
            try:
                decision = BoundaryDecision.model_validate(load_json(path))
            except Exception:
                continue
            results[decision.boundary_id] = decision
            break
    return results


def create_open_boundary_11_12(
    pdf_path: str | Path, settings: ProjectSettings, *, root: Path | None = None
) -> BoundaryDecision:
    root = (root or project_root()).resolve()
    _, context = _sample_context(pdf_path, settings, root)
    pages = load_all_page_results(settings, root)
    page11 = pages.get(11)
    if page11 is None:
        raise RuntimeError("Page 11 normalized result is required")
    record = _record(context, 11)
    _, _, last_id, tail = _body_edges(page11)
    decision = BoundaryDecision(
        schema_version=settings.boundary_schema_version,
        boundary_id="boundary_p0011_p0012_open",
        document_id=context.document_id,
        previous_page=11,
        next_page=12,
        next_page_available=False,
        previous_image_sha256=record.image_sha256,
        next_image_sha256=None,
        previous_last_block_id=last_id,
        next_first_block_id=None,
        previous_tail_text=tail,
        next_head_text="",
        word_continuation=None,
        sentence_continuation=True,
        paragraph_continuation=True,
        structural_break="unknown",
        join_operation="uncertain",
        hyphen_type="uncertain",
        header_footer_interference=None,
        reconstructed_boundary_text="",
        evidence=["The visible final sentence on sample page 11 is unfinished; page 12 is absent."],
        confidence=None,
        provider="local_open_boundary",
        model=None,
        review_window=[11],
        raw_response_path=None,
        normalization_events=[],
        model_review_status="not_called",
        human_review_status="required",
        needs_triple_review=False,
        status="open_boundary",
        missing_required_page=12,
        translation_blocked_reason="Sample page 12 is unavailable; the final sentence and paragraph cannot be completed.",
    )
    output = (
        resolve_project_path(settings.boundary_normalized_directory, root=root)
        / "open"
        / "p0011_p0012"
        / "open_boundary.json"
    )
    atomic_write_json(output, decision)
    return decision


def select_triple_candidates(
    pairs: dict[str, BoundaryDecision],
    maximum: int,
    extra_candidate_ids: set[str] | None = None,
) -> list[BoundaryDecision]:
    """Select only unresolved pair reviews, deterministically and within the hard cap."""

    extra_candidate_ids = extra_candidate_ids or set()
    candidates = [
        decision
        for decision in pairs.values()
        if decision.needs_triple_review
        or decision.model_review_status == "schema_failed"
        or decision.word_continuation is None
        or decision.sentence_continuation is None
        or decision.paragraph_continuation is None
        or decision.join_operation == "uncertain"
        or decision.boundary_id in extra_candidate_ids
    ]
    def priority(item: BoundaryDecision) -> tuple[int, int, int]:
        unresolved = (
            item.needs_triple_review
            or item.model_review_status == "schema_failed"
            or item.word_continuation is None
            or item.sentence_continuation is None
            or item.paragraph_continuation is None
            or item.join_operation == "uncertain"
        )
        contradictory = (
            item.structural_break != "none"
            and item.join_operation in {"concatenate_without_space", "concatenate_with_space"}
        )
        return (0 if unresolved else 1 if contradictory else 2, item.previous_page, item.next_page)

    return sorted(candidates, key=priority)[:maximum]


def _qa_mismatch_ids(settings: ProjectSettings, root: Path) -> set[str]:
    path = resolve_project_path(settings.boundary_qa_output_path, root=root)
    if not path.is_file():
        return set()
    record = load_json(path)
    return {
        str(item["boundary_id"])
        for item in record.get("items", [])
        if isinstance(item, dict) and item.get("matched") is False and item.get("boundary_id")
    }


def run_triple_boundaries(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    allow_api: bool = False,
    confirm_phase2b: bool = False,
    root: Path | None = None,
    provider_factory: Callable[..., Any] = ZhipuOpenAICompatibleProvider,
) -> BatchCallResult:
    root = (root or project_root()).resolve()
    _, context = _sample_context(pdf_path, settings, root)
    pages = load_all_page_results(settings, root)
    pairs = load_latest_boundaries(settings, root, "pair")
    candidates = select_triple_candidates(
        pairs,
        settings.phase2b_max_triple_calls,
        extra_candidate_ids=_qa_mismatch_ids(settings, root),
    )
    prompt = resolve_project_path(settings.boundary_prompt_path, root=root).read_text(encoding="utf-8")
    prompt_hash = sha256_text(prompt)
    api_calls = cache_hits = completed = needs_review = failed = 0
    failures: list[str] = []
    for pair in candidates:
        previous, next_page = pair.previous_page, pair.next_page
        if previous == 1:
            window = [1, 2, 3]
        elif next_page == 11:
            window = [9, 10, 11]
        else:
            window = [previous - 1, previous, next_page]
        records = [_record(context, page) for page in window]
        fingerprint = stable_hash(
            {
                "phase": "2B",
                "category": "triple",
                "boundary_id": pair.boundary_id,
                "window": window,
                "image_sha256": [record.image_sha256 for record in records],
                "pair_decision": pair.model_dump(mode="json"),
                "prompt_version": settings.boundary_prompt_version,
                "prompt_sha256": prompt_hash,
                "schema_version": settings.boundary_schema_version,
            }
        )
        paths = _boundary_paths(settings, root, "triple", previous, next_page, fingerprint)
        if paths["cache"].is_file() and paths["raw"].is_file() and paths["normalized"].is_file():
            cache_hits += 1
            completed += 1
            continue
        if not allow_api:
            continue
        if not confirm_phase2b:
            raise PermissionError("--confirm-phase2b is required for approved triple calls")
        previous_result, next_result = pages[previous], pages[next_page]
        context_payload = {
            "target_boundary": pair.model_dump(mode="json"),
            "review_window": window,
            "page_transcriptions": {str(page): pages[page].model_dump(mode="json") for page in window},
        }
        try:
            api_calls += 1
            response, _ = _call_provider_once(
                settings=settings,
                root=root,
                category="triple",
                item_id=pair.boundary_id,
                fingerprint=fingerprint,
                prompt=prompt,
                context_message=json.dumps(context_payload, ensure_ascii=False),
                image_paths=[Path(record.image_path) for record in records],
                raw_path=paths["raw"],
                request_path=paths["request"],
                usage_path=paths["usage"],
                provider_factory=provider_factory,
            )
            decision = _boundary_from_response(
                response,
                settings=settings,
                context=context,
                previous_record=_record(context, previous),
                next_record=_record(context, next_page),
                previous_result=previous_result,
                next_result=next_result,
                review_window=window,
                raw_path=paths["raw"],
                normalized_path=paths["normalized"],
                validation_path=paths["validation"],
            )
            atomic_write_json(
                paths["cache"],
                {
                    "request_fingerprint": fingerprint,
                    "api_call_completed": True,
                    "raw_response_path": str(paths["raw"].resolve()),
                    "normalized_output_path": str(paths["normalized"].resolve()),
                    "request_id": response.request_id,
                    "status": decision.status,
                },
            )
            completed += 1
            if decision.status == "needs_review":
                needs_review += 1
        except Exception as exc:
            failed += 1
            failures.append(f"{pair.boundary_id}:{type(exc).__name__}")
    return BatchCallResult(
        category="triple",
        requested=len(candidates),
        api_calls_started=api_calls,
        cache_hits=cache_hits,
        completed=completed,
        needs_review=needs_review,
        failed=failed,
        failed_items=failures,
    )
