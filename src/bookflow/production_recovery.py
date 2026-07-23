"""Narrow Phase 6 reconciliation helpers for terminal blank and manual recovery pages."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .fullbook_pipeline import (
    FullbookCheckpointStore,
    _fullbook_page_records,
    _load_fullbook_vision_ledger,
    _transcription_identity,
)
from .io_utils import atomic_write_json, load_json, sha256_file, stable_hash
from .paths import ProjectSettings, project_root, resolve_project_path
from .phase2a1 import normalize_preserved_response_v11
from .secret_store import load_api_key
from .vision_provider import ZhipuOpenAICompatibleProvider


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HumanVerifiedBlankState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fullbook-page-final-state-1.0"] = "fullbook-page-final-state-1.0"
    document_id: str
    pdf_page: int = Field(ge=1)
    source_pdf_sha256: str
    image_sha256: str
    resolution_type: Literal["human_verified_blank_page"] = "human_verified_blank_page"
    human_verified: Literal[True] = True
    verified_by: Literal["user"] = "user"
    visual_api_recall_required: Literal[False] = False
    page_type: Literal["blank"] = "blank"
    transcription_status: Literal["not_required_blank"] = "not_required_blank"
    text_blocks: list[Any] = Field(default_factory=list, max_length=0)
    source_fragments: list[Any] = Field(default_factory=list, max_length=0)
    nontext_content_present: Literal[False] = False
    translation_ready: Literal[False] = False
    quarantine: Literal[False] = False
    finalized_at: str


class AmbiguousAttemptClosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_page: int
    attempt_id: str
    original_status: Literal["in_flight"] = "in_flight"
    final_attempt_status: Literal["ambiguous_abandoned"] = "ambiguous_abandoned"
    resolution_type: Literal["ambiguous_request_abandoned"] = "ambiguous_request_abandoned"
    reason: Literal[
        "client_session_ended_without_raw_usage_or_normalized_artifacts"
    ] = "client_session_ended_without_raw_usage_or_normalized_artifacts"
    recoverable_from_local_artifacts: Literal[False] = False
    closed_at: str


class ManualRecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_page: int
    status: Literal["completed"] = "completed"
    attempt_id: str
    recovery_of_attempt_id: str
    request_fingerprint: str
    raw_response_path: str
    usage_path: str
    normalized_output_path: str
    cache_hit: bool
    api_calls_this_run: int = Field(ge=0, le=1)
    total_tokens_this_run: int = Field(ge=0)


def _records(settings: ProjectSettings, root: Path) -> dict[int, dict[str, Any]]:
    return {int(item["pdf_page"]): item for item in _fullbook_page_records(settings, root)}


def _attempt_id(attempt: dict[str, Any]) -> str:
    value = attempt.get("attempt_id")
    if isinstance(value, str) and value:
        return value
    return "attempt_" + stable_hash({
        "pdf_page": attempt.get("pdf_page"),
        "request_fingerprint": attempt.get("request_fingerprint"),
        "started_at": attempt.get("started_at"),
    })[:24]


def finalize_human_verified_blank(
    settings: ProjectSettings, *, pdf_page: int, root: Path | None = None
) -> HumanVerifiedBlankState:
    """Persist a user-confirmed blank terminal state without any model call."""

    root = (root or project_root()).resolve()
    record = _records(settings, root).get(pdf_page)
    if record is None:
        raise FileNotFoundError(f"Rendered page record missing: {pdf_page}")
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    final_path = output / "final_states" / f"page_{pdf_page:04d}.json"
    if final_path.is_file():
        return HumanVerifiedBlankState.model_validate(load_json(final_path))
    state = HumanVerifiedBlankState(
        document_id=str(record["document_id"]), pdf_page=pdf_page,
        source_pdf_sha256=str(record["source_pdf_sha256"]),
        image_sha256=str(record["image_sha256"]), finalized_at=_now(),
    )
    atomic_write_json(final_path, state)
    ledger_path = output / "call_ledger.json"
    ledger = _load_fullbook_vision_ledger(ledger_path, state.source_pdf_sha256)
    finalizations = ledger.setdefault("page_finalizations", [])
    if not any(item.get("pdf_page") == pdf_page for item in finalizations):
        finalizations.append({
            "pdf_page": pdf_page, "resolution_type": state.resolution_type,
            "human_verified": True, "verified_by": "user",
            "visual_api_recall_required": False, "final_state_path": str(final_path.resolve()),
            "finalized_at": state.finalized_at,
        })
        ledger["updated_at"] = _now()
        atomic_write_json(ledger_path, ledger)
    atomic_write_json(output / "cache" / f"page_{pdf_page:04d}.json", {
        "status": "final_blank", "document_id": state.document_id, "pdf_page": pdf_page,
        "image_sha256": state.image_sha256, "final_state_path": str(final_path.resolve()),
        "resolution_type": state.resolution_type, "api_called_this_run": False,
        "created_at": state.finalized_at,
    })
    checkpoint = FullbookCheckpointStore(
        resolve_project_path(settings.fullbook_checkpoint_path, root=root),
        source_pdf_sha256=state.source_pdf_sha256,
    )
    checkpoint.mark_completed("vision_single", f"page_{pdf_page:04d}")
    checkpoint.clear_quarantine("vision_single", f"page_{pdf_page:04d}")
    return state


def close_ambiguous_inflight(
    settings: ProjectSettings, *, pdf_page: int, root: Path | None = None
) -> AmbiguousAttemptClosure:
    """Close one locally orphaned in-flight attempt without pretending it failed or succeeded."""

    root = (root or project_root()).resolve()
    record = _records(settings, root).get(pdf_page)
    if record is None:
        raise FileNotFoundError(f"Rendered page record missing: {pdf_page}")
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    ledger_path = output / "call_ledger.json"
    ledger = _load_fullbook_vision_ledger(ledger_path, str(record["source_pdf_sha256"]))
    matches = [item for item in ledger.get("attempts", []) if int(item.get("pdf_page", 0)) == pdf_page]
    ambiguous = [item for item in matches if item.get("status") == "ambiguous_abandoned"]
    if ambiguous:
        item = ambiguous[-1]
        return AmbiguousAttemptClosure(
            pdf_page=pdf_page, attempt_id=str(item["attempt_id"]), closed_at=str(item["closed_at"])
        )
    inflight = [item for item in matches if item.get("status") == "in_flight"]
    if len(inflight) != 1:
        raise RuntimeError(f"Expected exactly one in-flight attempt for page {pdf_page}")
    item = inflight[0]
    fingerprint = str(item.get("request_fingerprint", ""))
    local_artifacts = [
        output / "raw" / f"{fingerprint}.json",
        output / "usage" / f"page_{pdf_page:04d}_{fingerprint}.json",
        output / "normalized" / f"page_{pdf_page:04d}_{fingerprint}.json",
        output / "cache" / f"page_{pdf_page:04d}.json",
    ]
    if any(path.is_file() for path in local_artifacts):
        raise RuntimeError(f"Page {pdf_page} has local artifacts and cannot be closed as artifact-free")
    attempt_id = _attempt_id(item)
    closed_at = _now()
    item.update({
        "attempt_id": attempt_id, "original_status": "in_flight",
        "status": "ambiguous_abandoned", "final_attempt_status": "ambiguous_abandoned",
        "resolution_type": "ambiguous_request_abandoned",
        "reason": "client_session_ended_without_raw_usage_or_normalized_artifacts",
        "recoverable_from_local_artifacts": False, "closed_at": closed_at,
    })
    ledger["updated_at"] = closed_at
    atomic_write_json(ledger_path, ledger)
    return AmbiguousAttemptClosure(pdf_page=pdf_page, attempt_id=attempt_id, closed_at=closed_at)


def run_manual_page_recovery(
    settings: ProjectSettings,
    *,
    pdf_page: int,
    recovery_reason: str,
    root: Path | None = None,
    allow_api: bool = False,
    provider_factory: Callable[..., Any] = ZhipuOpenAICompatibleProvider,
    api_key_loader: Callable[..., tuple[str, str]] = load_api_key,
) -> ManualRecoveryResult:
    """Perform at most one explicitly approved manual recovery request for one page."""

    root = (root or project_root()).resolve()
    record = _records(settings, root).get(pdf_page)
    if record is None:
        raise FileNotFoundError(f"Rendered page record missing: {pdf_page}")
    source_hash = str(record["source_pdf_sha256"])
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    ledger_path = output / "call_ledger.json"
    ledger = _load_fullbook_vision_ledger(ledger_path, source_hash)
    page_attempts = [item for item in ledger.get("attempts", []) if int(item.get("pdf_page", 0)) == pdf_page]
    existing = [item for item in page_attempts if item.get("attempt_type") == "manual_recovery"]
    if existing:
        attempt = existing[-1]
        if attempt.get("status") != "completed":
            raise RuntimeError(f"Page {pdf_page} already consumed its one manual recovery attempt")
        cache = load_json(output / "cache" / f"page_{pdf_page:04d}.json")
        return ManualRecoveryResult(
            pdf_page=pdf_page, attempt_id=str(attempt["attempt_id"]),
            recovery_of_attempt_id=str(attempt["recovery_of_attempt_id"]),
            request_fingerprint=str(attempt["request_fingerprint"]),
            raw_response_path=str(cache["raw_response_path"]), usage_path=str(cache["usage_path"]),
            normalized_output_path=str(cache["normalized_output_path"]), cache_hit=True,
            api_calls_this_run=0, total_tokens_this_run=0,
        )
    origins = [item for item in page_attempts if item.get("status") in {"failed", "ambiguous_abandoned"}]
    if not origins:
        cache_path = output / "cache" / f"page_{pdf_page:04d}.json"
        if cache_path.is_file() and load_json(cache_path).get("status") == "completed":
            raise RuntimeError(f"Page {pdf_page} is already completed and must not be recovered")
        raise RuntimeError(f"Page {pdf_page} has no failed or ambiguous attempt to recover")
    origin = origins[-1]
    origin_id = _attempt_id(origin)
    origin.setdefault("attempt_id", origin_id)
    base_fingerprint = _transcription_identity(settings, str(record["image_sha256"]), root)
    fingerprint = stable_hash({
        "base_fingerprint": base_fingerprint, "attempt_type": "manual_recovery",
        "recovery_of_attempt_id": origin_id, "recovery_reason": recovery_reason,
        "approved_by_user": True,
    })
    attempt_id = "attempt_" + stable_hash({"request_fingerprint": fingerprint, "pdf_page": pdf_page})[:24]
    if not allow_api:
        raise PermissionError("real vision API is disabled for pending manual recovery")
    key, key_name = api_key_loader(settings, root)
    client = provider_factory(
        api_key=key, base_url=settings.vision_base_url,
        timeout_seconds=settings.vision_request_timeout_seconds,
    )
    prompt_path = root / "prompts" / "vision_transcription_v2.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    raw_path = output / "raw" / f"{fingerprint}.json"
    normalized_path = output / "normalized" / f"page_{pdf_page:04d}_{fingerprint}.json"
    usage_path = output / "usage" / f"page_{pdf_page:04d}_{fingerprint}.json"
    request_path = output / "request" / f"page_{pdf_page:04d}_{fingerprint}.json"
    error_path = output / "errors" / f"page_{pdf_page:04d}_{fingerprint}.json"
    started_at = _now()
    attempt = {
        "attempt_id": attempt_id, "pdf_page": pdf_page, "request_fingerprint": fingerprint,
        "status": "in_flight", "attempt_type": "manual_recovery",
        "recovery_of_attempt_id": origin_id, "recovery_reason": recovery_reason,
        "automatic_retry": False, "approved_by_user": True, "started_at": started_at,
    }
    ledger["real_calls_started"] = int(ledger.get("real_calls_started", 0)) + 1
    ledger.setdefault("attempts", []).append(attempt)
    ledger["updated_at"] = started_at
    atomic_write_json(ledger_path, ledger)
    checkpoint = FullbookCheckpointStore(
        resolve_project_path(settings.fullbook_checkpoint_path, root=root),
        source_pdf_sha256=source_hash,
    )
    checkpoint.increment_api_call("single")
    request_metadata = {
        "schema_version": "fullbook-vision-manual-recovery-request-1.0", "phase": "6",
        "status": "in_flight", "attempt_id": attempt_id,
        "attempt_type": "manual_recovery", "recovery_of_attempt_id": origin_id,
        "recovery_reason": recovery_reason, "approved_by_user": True,
        "request_fingerprint": fingerprint, "provider": settings.vision_provider,
        "model": settings.vision_model, "base_url": settings.vision_base_url,
        "api_key_env": key_name, "api_key_recorded": False, "pdf_page": pdf_page,
        "document_id": record["document_id"], "source_pdf_sha256": source_hash,
        "image_path": record["image_path"], "image_sha256": record["image_sha256"],
        "prompt_path": str(prompt_path.resolve()), "prompt_sha256": sha256_file(prompt_path),
        "automatic_retry": False, "started_at": started_at,
    }
    atomic_write_json(request_path, request_metadata)
    try:
        image_data = "data:image/png;base64," + base64.b64encode(
            Path(str(record["image_path"])).read_bytes()
        ).decode("ascii")
        context = (
            f"Technical context only: document_id={record['document_id']}; "
            f"pdf_page={pdf_page}; provider={settings.vision_provider}; "
            f"model={settings.vision_model}; schema_version=2.0. Use only the attached image."
        )
        response = client.transcribe_one_page(
            model=settings.vision_model, prompt=prompt, context_message=context,
            image_data_url=image_data, max_output_tokens=settings.vision_max_output_tokens,
            temperature=settings.vision_temperature, do_sample=settings.vision_do_sample,
            thinking_mode=settings.vision_thinking_mode,
            response_format_json_object=settings.vision_response_format_json_object,
        )
        atomic_write_json(raw_path, {
            "record_type": "raw_provider_response", "request_fingerprint": fingerprint,
            "provider": settings.vision_provider, "model": settings.vision_model,
            "api_called": True, "received_at": _now(), "response": response.raw_response,
        })
        normalized = normalize_preserved_response_v11(raw_path, normalized_path)
        usage = response.usage or {}
        atomic_write_json(usage_path, {
            "request_fingerprint": fingerprint, "request_id": response.request_id,
            "usage": usage, "cash_charge_confirmed": False, "cash_charge_cny": None,
        })
        ended_at = _now()
        request_metadata.update({
            "status": "completed", "request_id": response.request_id, "ended_at": ended_at,
            "raw_response_path": str(raw_path.resolve()),
            "normalized_output_path": str(normalized_path.resolve()), "retries": 0,
        })
        atomic_write_json(request_path, request_metadata)
        attempt.update({"status": "completed", "ended_at": ended_at, "request_id": response.request_id})
        ledger["updated_at"] = ended_at
        atomic_write_json(ledger_path, ledger)
        cache_path = output / "cache" / f"page_{pdf_page:04d}.json"
        atomic_write_json(cache_path, {
            "status": "completed", "request_fingerprint": fingerprint,
            "document_id": record["document_id"], "pdf_page": pdf_page,
            "image_sha256": record["image_sha256"], "raw_response_path": str(raw_path.resolve()),
            "normalized_output_path": str(normalized_path.resolve()),
            "usage_path": str(usage_path.resolve()), "api_called_this_run": True,
            "normalized_status": normalized.status, "attempt_type": "manual_recovery",
            "recovery_of_attempt_id": origin_id, "created_at": ended_at,
        })
        checkpoint.mark_completed("vision_single", f"page_{pdf_page:04d}")
        checkpoint.clear_quarantine("vision_single", f"page_{pdf_page:04d}")
        return ManualRecoveryResult(
            pdf_page=pdf_page, attempt_id=attempt_id, recovery_of_attempt_id=origin_id,
            request_fingerprint=fingerprint, raw_response_path=str(raw_path.resolve()),
            usage_path=str(usage_path.resolve()), normalized_output_path=str(normalized_path.resolve()),
            cache_hit=False, api_calls_this_run=1,
            total_tokens_this_run=int(usage.get("total_tokens") or 0),
        )
    except Exception as exc:
        safe = str(exc).replace(key, "[REDACTED]")[:1000]
        ended_at = _now()
        attempt.update({"status": "failed", "ended_at": ended_at, "error_type": type(exc).__name__})
        ledger["updated_at"] = ended_at
        atomic_write_json(ledger_path, ledger)
        atomic_write_json(error_path, {
            "record_type": "manual_recovery_error", "request_fingerprint": fingerprint,
            "attempt_id": attempt_id, "pdf_page": pdf_page, "error_type": type(exc).__name__,
            "error_message": safe, "api_called": True, "automatic_retry": False,
            "recorded_at": ended_at,
        })
        checkpoint.mark_quarantine("vision_single", f"page_{pdf_page:04d}", safe)
        raise RuntimeError(f"Manual recovery failed for page {pdf_page}: {safe}") from exc


def find_dynamic_vision_resume_page(
    settings: ProjectSettings, *, actual_page_count: int, root: Path | None = None
) -> int | None:
    """Return the first page with no terminal cache/final state and no explicit quarantine."""

    root = (root or project_root()).resolve()
    records = _records(settings, root)
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    if not records:
        raise RuntimeError("No rendered full-book page records exist")
    source_hash = str(next(iter(records.values()))["source_pdf_sha256"])
    ledger = _load_fullbook_vision_ledger(output / "call_ledger.json", source_hash)
    inflight = [item for item in ledger.get("attempts", []) if item.get("status") == "in_flight"]
    if inflight:
        raise RuntimeError(f"Unresolved in-flight attempts: {[item.get('pdf_page') for item in inflight]}")
    checkpoint = FullbookCheckpointStore(
        resolve_project_path(settings.fullbook_checkpoint_path, root=root),
        source_pdf_sha256=source_hash,
    ).payload
    quarantined = set(checkpoint.get("quarantine", {}).get("vision_single", {}))
    for page in range(1, actual_page_count + 1):
        record = records.get(page)
        if record is None:
            return page
        cache_path = output / "cache" / f"page_{page:04d}.json"
        terminal = False
        if cache_path.is_file():
            cache = load_json(cache_path)
            if cache.get("image_sha256") == record.get("image_sha256"):
                if cache.get("status") == "completed":
                    terminal = Path(str(cache.get("raw_response_path", ""))).is_file() and Path(
                        str(cache.get("normalized_output_path", ""))
                    ).is_file()
                elif cache.get("status") == "final_blank":
                    terminal = Path(str(cache.get("final_state_path", ""))).is_file()
        if terminal:
            continue
        if f"page_{page:04d}" in quarantined:
            continue
        return page
    return None


def derive_effective_text_boundaries(page_states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe textual adjacency when one or more physical nontext pages intervene."""

    ordered = sorted(page_states, key=lambda item: int(item["pdf_page"]))
    text_pages = [item for item in ordered if bool(item.get("has_text"))]
    results: list[dict[str, Any]] = []
    by_page = {int(item["pdf_page"]): item for item in ordered}
    for left, right in zip(text_pages, text_pages[1:]):
        previous = int(left["pdf_page"])
        following = int(right["pdf_page"])
        skipped = list(range(previous + 1, following))
        if skipped and all(not bool(by_page[page].get("has_text")) for page in skipped):
            results.append({
                "previous_text_page": previous, "next_text_page": following,
                "boundary_kind": "textual_adjacency_across_nontext_pages",
                "physical_pages_skipped": skipped,
            })
    return results
