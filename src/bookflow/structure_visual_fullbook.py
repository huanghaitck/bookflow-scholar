"""Phase 1D: Full-book visual structure scan.

Orchestrates re-calling 3 Phase 1C failure pages and scanning 48 remaining
visual targets, then merges all 58 results into a unified set and generates
a 412-page structure merge preview.

Key constraints:
- MAX 51 new API calls (3 re-call + 48 remaining)
- 7 Phase 1C success pages are NOT re-called
- automatic_retry = false, sequential execution
- API key never persisted; data URLs never persisted
- page_map, bridge_candidates, and boundaries are never modified
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file
from .paths import ProjectSettings, load_settings
from .secret_store import load_api_key
from .structure_visual_live import (
    LIVE_RUNNER_VERSION,
    BatchRunResult,
    ContentError,
    LedgerEntry,
    LiveProviderConfig,
    SystemError_,
    build_live_config,
    build_live_system_prompt,
    load_call_ledger,
    run_live_sample_batch,
    _compute_prompt_file_sha,
    _compute_schema_sha,
    _compute_system_prompt_sha,
    _now_iso,
    _page_filename,
)
from .structure_visual_requests import build_visual_page_request
from .structure_visual_schemas import VisualPageClassificationRequest

PHASE1D_MAX_CALLS = 51
PHASE1C_SUCCESS_PAGES = frozenset({1, 8, 119, 340, 380, 384, 412})
PHASE1C_FAILURE_PAGES = frozenset({120, 198, 401})
PHASE1D_BASE = "data/fullbook/structure/phase1d"
PHASE1C_BASE = "data/fullbook/structure/phase1c/live/sample_batch_v1"

_RECALL_PAGES = sorted(PHASE1C_FAILURE_PAGES)
_REMAINING_PAGES: list[int] | None = None


def _compute_remaining_pages() -> list[int]:
    """Compute the 48 remaining pages (58 - 7 success - 3 failure)."""
    all_58 = {
        1, 2, 4, 5, 8, 10, 16, 20, 23, 44, 66, 90, 98, 104, 110, 116, 119, 120,
        124, 174, 184, 187, 188, 198, 204, 208, 213, 214, 220, 232, 244, 247,
        248, 256, 262, 270, 278, 288, 312, 324, 328, 334, 338, 340, 348, 354,
        380, 384, 387, 390, 401, 402, 403, 404, 409, 410, 411, 412,
    }
    remaining = all_58 - PHASE1C_SUCCESS_PAGES - PHASE1C_FAILURE_PAGES
    return sorted(remaining)


def get_remaining_pages() -> list[int]:
    global _REMAINING_PAGES
    if _REMAINING_PAGES is None:
        _REMAINING_PAGES = _compute_remaining_pages()
    return list(_REMAINING_PAGES)


@dataclass
class PhaseDRunResult:
    recall_pages: list[int]
    remaining_pages: list[int]
    recall_succeeded: list[int]
    recall_content_errors: list[int]
    recall_system_errors: list[int]
    remaining_succeeded: list[int]
    remaining_content_errors: list[int]
    remaining_system_errors: list[int]
    total_api_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    gate_passed: bool
    gate_messages: list[str]
    stopped_due_to_system_error: bool
    automatic_retries: int
    started_at: str
    completed_at: str


def _load_targets(root: Path) -> list[dict]:
    path = root / "data/fullbook/structure/phase1c/ambiguous_page_targets.jsonl"
    targets: list[dict] = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            targets.append(json.loads(line))
    return targets


def _load_page_map(root: Path) -> dict[int, dict]:
    path = root / "data/fullbook/structure/registry/page_map.jsonl"
    records: dict[int, dict] = {}
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            records[d["physical_page"]] = d
    return records


def build_all_58_requests(root: Path) -> list[VisualPageClassificationRequest]:
    """Build requests for all 58 visual targets."""
    targets = _load_targets(root)
    page_map = _load_page_map(root)
    requests: list[VisualPageClassificationRequest] = []
    for target in targets:
        req = build_visual_page_request(target, page_map_record=page_map)
        requests.append(req)
    return requests


def get_recall_requests(
    root: Path,
) -> list[VisualPageClassificationRequest]:
    """Return the 3 re-call requests for p120, p198, p401."""
    all_reqs = build_all_58_requests(root)
    return [r for r in all_reqs if r.physical_page in PHASE1C_FAILURE_PAGES]


def get_remaining_requests(
    root: Path,
) -> list[VisualPageClassificationRequest]:
    """Return the 48 remaining-page requests."""
    all_reqs = build_all_58_requests(root)
    return [
        r for r in all_reqs
        if r.physical_page not in PHASE1C_SUCCESS_PAGES
        and r.physical_page not in PHASE1C_FAILURE_PAGES
    ]


def build_phase1d_config(settings: ProjectSettings) -> LiveProviderConfig:
    """Create a LiveProviderConfig with max_calls=51."""
    base = build_live_config(settings)
    return LiveProviderConfig(
        provider_id=base.provider_id,
        base_url=base.base_url,
        model=base.model,
        api_key_env=base.api_key_env,
        timeout_seconds=base.timeout_seconds,
        max_calls=PHASE1D_MAX_CALLS,
        response_format_json_object=base.response_format_json_object,
        max_output_tokens=base.max_output_tokens,
        do_sample=base.do_sample,
        thinking_mode=base.thinking_mode,
    )


def _check_recall_gate(
    root: Path,
    recall_result: BatchRunResult,
) -> tuple[bool, list[str]]:
    """Check if the 3 re-called pages pass the acceptance gate."""
    messages: list[str] = []

    all_succeeded = set(recall_result.succeeded) | set(recall_result.already_successful)
    if len(all_succeeded) != 3:
        messages.append(
            f"Expected 3 successes, got {len(all_succeeded)} "
            f"(new={len(recall_result.succeeded)}, "
            f"already={len(recall_result.already_successful)}); "
            f"content_errors={recall_result.content_errors}, "
            f"system_errors={recall_result.system_errors}"
        )
        return False, messages

    for pg in [120, 198, 401]:
        norm_path = root / PHASE1D_BASE / "normalized" / f"{_page_filename(pg)}.json"
        if not norm_path.is_file():
            messages.append(f"p{pg}: normalized response not found at {norm_path}")
            return False, messages
        norm_data = json.loads(norm_path.read_text("utf-8"))
        resp = norm_data.get("response", {})
        role = resp.get("primary_role", "")
        contains_prose = resp.get("contains_prose", True)
        safe_exclude = resp.get("safe_to_exclude_from_prose_flow", False)
        requires_region = resp.get("requires_region_analysis", False)

        if pg in (120, 198):
            if role == "blank":
                pass
            elif not contains_prose and safe_exclude:
                pass
            else:
                messages.append(
                    f"p{pg}: expected blank-like result, got "
                    f"primary_role={role}, contains_prose={contains_prose}"
                )
                return False, messages

        if pg == 401:
            if role not in ("appendix", "table"):
                messages.append(
                    f"p401: expected appendix or table, got primary_role={role}"
                )
                return False, messages
            content_feats = resp.get("content_features", [])
            if not requires_region and "table" not in content_feats:
                messages.append(
                    f"p401 NOTE: role=appendix confirmed but model did not identify "
                    f"table content or set requires_region_analysis; "
                    f"flagged for human review (content_features={content_feats})"
                )

    return True, messages


def run_phase1d_scan(
    *,
    root: Path,
    settings: ProjectSettings | None = None,
    dry_run: bool = False,
    client_factory: Any = OpenAI,
) -> PhaseDRunResult:
    """Run the full Phase 1D scan.

    Sequentially calls 3 re-call pages, checks the acceptance gate,
    then calls 48 remaining pages.
    """
    if settings is None:
        settings = load_settings()

    started_at = _now_iso()
    config = build_phase1d_config(settings)

    all_requests = build_all_58_requests(root)
    assert len(all_requests) == 58, f"Expected 58 requests, got {len(all_requests)}"

    recall_requests = [
        r for r in all_requests if r.physical_page in PHASE1C_FAILURE_PAGES
    ]
    remaining_requests = [
        r for r in all_requests
        if r.physical_page not in PHASE1C_SUCCESS_PAGES
        and r.physical_page not in PHASE1C_FAILURE_PAGES
    ]
    assert len(recall_requests) == 3
    assert len(remaining_requests) == 48

    recall_pages_set = {r.physical_page for r in recall_requests}
    remaining_pages_set = {r.physical_page for r in remaining_requests}
    assert not (recall_pages_set & remaining_pages_set), "Overlap detected"

    prompt_file_sha = _compute_prompt_file_sha(root)
    system_prompt_sha = _compute_system_prompt_sha(root)
    schema_sha = _compute_schema_sha()

    phase1d_dir = root / PHASE1D_BASE
    phase1d_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "normalized", "errors", "usage", "request_metadata", "requests"):
        (phase1d_dir / sub).mkdir(parents=True, exist_ok=True)

    ledger_path = phase1d_dir / "call_ledger.json"
    ledger = load_call_ledger(ledger_path)

    checkpoint = {
        "phase": "phase_1d",
        "status": "started",
        "started_at": started_at,
        "total_targets": 58,
        "recall_pages": sorted(recall_pages_set),
        "remaining_pages": sorted(remaining_pages_set),
        "phase1c_success_pages": sorted(PHASE1C_SUCCESS_PAGES),
        "max_calls": PHASE1D_MAX_CALLS,
        "runner_version": LIVE_RUNNER_VERSION,
        "prompt_file_sha": prompt_file_sha,
        "system_prompt_sha": system_prompt_sha,
        "schema_sha": schema_sha,
    }
    atomic_write_json(phase1d_dir / "checkpoint.json", checkpoint)

    if dry_run:
        return PhaseDRunResult(
            recall_pages=sorted(recall_pages_set),
            remaining_pages=sorted(remaining_pages_set),
            recall_succeeded=[],
            recall_content_errors=[],
            recall_system_errors=[],
            remaining_succeeded=[],
            remaining_content_errors=[],
            remaining_system_errors=[],
            total_api_calls=0,
            total_prompt_tokens=0,
            total_completion_tokens=0,
            total_tokens=0,
            gate_passed=False,
            gate_messages=["dry_run"],
            stopped_due_to_system_error=False,
            automatic_retries=0,
            started_at=started_at,
            completed_at=_now_iso(),
        )

    api_key, _ = load_api_key(settings, root)
    system_prompt = build_live_system_prompt(root)

    recall_result = run_live_sample_batch(
        root=root,
        settings=settings,
        config=config,
        requests=recall_requests,
        ledger=ledger,
        prompt_file_sha=prompt_file_sha,
        system_prompt_sha=system_prompt_sha,
        schema_sha=schema_sha,
        client_factory=client_factory,
        dry_run=False,
        system_prompt=system_prompt,
        api_key=api_key,
        base_dir=phase1d_dir,
    )

    checkpoint["status"] = "recall_completed"
    checkpoint["recall_result"] = {
        "succeeded": recall_result.succeeded,
        "content_errors": recall_result.content_errors,
        "system_errors": recall_result.system_errors,
        "actual_api_calls": recall_result.actual_api_calls,
    }
    atomic_write_json(phase1d_dir / "checkpoint.json", checkpoint)

    gate_passed, gate_messages = _check_recall_gate(root, recall_result)

    if not gate_passed:
        completed_at = _now_iso()
        checkpoint["status"] = "blocked_at_gate"
        checkpoint["gate_messages"] = gate_messages
        atomic_write_json(phase1d_dir / "checkpoint.json", checkpoint)
        return PhaseDRunResult(
            recall_pages=sorted(recall_pages_set),
            remaining_pages=sorted(remaining_pages_set),
            recall_succeeded=recall_result.succeeded,
            recall_content_errors=recall_result.content_errors,
            recall_system_errors=recall_result.system_errors,
            remaining_succeeded=[],
            remaining_content_errors=[],
            remaining_system_errors=[],
            total_api_calls=recall_result.actual_api_calls,
            total_prompt_tokens=recall_result.total_prompt_tokens,
            total_completion_tokens=recall_result.total_completion_tokens,
            total_tokens=recall_result.total_tokens,
            gate_passed=False,
            gate_messages=gate_messages,
            stopped_due_to_system_error=recall_result.stopped_due_to_system_error,
            automatic_retries=0,
            started_at=started_at,
            completed_at=completed_at,
        )

    remaining_result = run_live_sample_batch(
        root=root,
        settings=settings,
        config=config,
        requests=remaining_requests,
        ledger=ledger,
        prompt_file_sha=prompt_file_sha,
        system_prompt_sha=system_prompt_sha,
        schema_sha=schema_sha,
        client_factory=client_factory,
        dry_run=False,
        system_prompt=system_prompt,
        api_key=api_key,
        base_dir=phase1d_dir,
    )

    completed_at = _now_iso()
    total_calls = recall_result.actual_api_calls + remaining_result.actual_api_calls

    checkpoint["status"] = "completed"
    checkpoint["total_api_calls"] = total_calls
    checkpoint["remaining_result"] = {
        "succeeded": remaining_result.succeeded,
        "content_errors": remaining_result.content_errors,
        "system_errors": remaining_result.system_errors,
        "actual_api_calls": remaining_result.actual_api_calls,
    }
    atomic_write_json(phase1d_dir / "checkpoint.json", checkpoint)

    return PhaseDRunResult(
        recall_pages=sorted(recall_pages_set),
        remaining_pages=sorted(remaining_pages_set),
        recall_succeeded=recall_result.succeeded,
        recall_content_errors=recall_result.content_errors,
        recall_system_errors=recall_result.system_errors,
        remaining_succeeded=remaining_result.succeeded,
        remaining_content_errors=remaining_result.content_errors,
        remaining_system_errors=remaining_result.system_errors,
        total_api_calls=total_calls,
        total_prompt_tokens=recall_result.total_prompt_tokens + remaining_result.total_prompt_tokens,
        total_completion_tokens=recall_result.total_completion_tokens + remaining_result.total_completion_tokens,
        total_tokens=recall_result.total_tokens + remaining_result.total_tokens,
        gate_passed=True,
        gate_messages=gate_messages,
        stopped_due_to_system_error=remaining_result.stopped_due_to_system_error,
        automatic_retries=0,
        started_at=started_at,
        completed_at=completed_at,
    )


def _read_normalized(root: Path, ref: str | None) -> dict | None:
    if ref is None:
        return None
    path = root / ref
    if not path.is_file():
        return None
    return json.loads(path.read_text("utf-8"))


def _extract_result_fields(
    root: Path,
    pg: int,
    source_phase: str,
    entry: LedgerEntry,
) -> dict[str, Any]:
    """Build a result record from a ledger entry."""
    norm = _read_normalized(root, entry.normalized_response_ref)
    if entry.status == "success" and norm:
        resp = norm.get("response", {})
        return {
            "physical_page": pg,
            "source_phase": source_phase,
            "request_id": entry.request_id,
            "offline_request_fingerprint": entry.offline_request_fingerprint,
            "live_call_fingerprint": entry.live_call_fingerprint,
            "status": "success",
            "primary_role": resp.get("primary_role"),
            "blank_kind": resp.get("blank_kind"),
            "content_features": resp.get("content_features", []),
            "artifact_overlays": resp.get("artifact_overlays", []),
            "original_book_content": resp.get("original_book_content"),
            "contains_prose": resp.get("contains_prose"),
            "safe_to_exclude_from_prose_flow": resp.get("safe_to_exclude_from_prose_flow"),
            "requires_region_analysis": resp.get("requires_region_analysis"),
            "printed_page_label": resp.get("printed_page_label"),
            "printed_page_number": resp.get("printed_page_number"),
            "numbering_scheme": resp.get("numbering_scheme"),
            "page_side": resp.get("page_side"),
            "confidence_by_field": resp.get("confidence_by_field", {}),
            "warnings": resp.get("warnings", []),
            "raw_response_ref": entry.raw_response_ref,
            "normalized_response_ref": entry.normalized_response_ref,
            "usage_ref": entry.usage_ref,
            "review_status": "pending_human_review",
        }
    else:
        return {
            "physical_page": pg,
            "source_phase": source_phase,
            "request_id": entry.request_id,
            "offline_request_fingerprint": entry.offline_request_fingerprint,
            "live_call_fingerprint": entry.live_call_fingerprint,
            "status": entry.status,
            "primary_role": None,
            "blank_kind": None,
            "content_features": [],
            "artifact_overlays": [],
            "original_book_content": None,
            "contains_prose": None,
            "safe_to_exclude_from_prose_flow": None,
            "requires_region_analysis": None,
            "printed_page_label": None,
            "printed_page_number": None,
            "numbering_scheme": None,
            "page_side": None,
            "confidence_by_field": {},
            "warnings": [],
            "raw_response_ref": entry.raw_response_ref,
            "normalized_response_ref": entry.normalized_response_ref,
            "usage_ref": entry.usage_ref,
            "review_status": "pending_human_review",
        }


def generate_all_58_results(root: Path) -> Path:
    """Merge 7 Phase 1C successes + 51 Phase 1D results."""
    phase1c_ledger = load_call_ledger(
        root / PHASE1C_BASE / "call_ledger.json"
    )
    phase1d_ledger = load_call_ledger(
        root / PHASE1D_BASE / "call_ledger.json"
    )

    results: list[dict[str, Any]] = []

    for pg in sorted(PHASE1C_SUCCESS_PAGES):
        entry = phase1c_ledger.get(pg)
        if entry and entry.status == "success":
            results.append(_extract_result_fields(root, pg, "phase_1c_existing_success", entry))

    for pg, entry in sorted(phase1d_ledger.items()):
        if pg in PHASE1C_SUCCESS_PAGES:
            continue
        if pg in PHASE1C_FAILURE_PAGES:
            source_phase = "phase_1d_reprocessed_failure"
        else:
            source_phase = "phase_1d_fullbook_scan"
        results.append(_extract_result_fields(root, pg, source_phase, entry))

    results.sort(key=lambda r: r["physical_page"])

    output_path = root / PHASE1D_BASE / "all_58_visual_results.jsonl"
    atomic_write_jsonl(output_path, results)
    return output_path


def generate_merge_preview(root: Path) -> Path:
    """Generate 412-page structure merge preview."""
    page_map = _load_page_map(root)

    vis_path = root / PHASE1D_BASE / "all_58_visual_results.jsonl"
    visual_results: dict[int, dict] = {}
    if vis_path.is_file():
        for line in vis_path.read_text("utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                visual_results[d["physical_page"]] = d

    targets = _load_targets(root)
    target_pages = {t["physical_page"] for t in targets}

    _FIELDS_FROM_VISUAL = (
        "primary_role", "blank_kind", "content_features", "artifact_overlays",
        "original_book_content", "contains_prose", "requires_region_analysis",
        "printed_page_label", "printed_page_number", "numbering_scheme",
        "page_side",
    )

    preview: list[dict[str, Any]] = []
    for pg in range(1, 413):
        pm = page_map.get(pg)
        if pm is None:
            continue

        vis = visual_results.get(pg)

        if pg in target_pages and vis and vis.get("status") == "success":
            current = {k: pm.get(k) for k in _FIELDS_FROM_VISUAL}
            current["classification_source"] = pm.get("classification_source")
            current["confidence_by_field"] = pm.get("confidence_by_field", {})
            current["evidence"] = pm.get("evidence", [])
            current["requires_followup"] = pm.get("requires_followup", False)

            proposed = {k: vis.get(k) for k in _FIELDS_FROM_VISUAL}
            proposed["classification_source"] = "visual_api"
            proposed["confidence_by_field"] = vis.get("confidence_by_field", {})
            proposed["evidence"] = vis.get("warnings", [])
            proposed["requires_followup"] = vis.get("requires_region_analysis", False)

            preview.append({
                "physical_page": pg,
                "is_visual_target": True,
                "visual_status": "success",
                "current_value": current,
                "proposed_value": proposed,
                "derivation_rule": "visual_api_classification",
                "evidence_ref": vis.get("raw_response_ref"),
                "review_required": True,
                "visual_resolution_pending": False,
            })
        elif pg in target_pages and vis:
            preview.append({
                "physical_page": pg,
                "is_visual_target": True,
                "visual_status": vis.get("status", "unknown"),
                "current_value": pm,
                "proposed_value": None,
                "derivation_rule": "keep_phase1b_value",
                "evidence_ref": None,
                "review_required": True,
                "visual_resolution_pending": True,
            })
        elif pg in target_pages:
            preview.append({
                "physical_page": pg,
                "is_visual_target": True,
                "visual_status": "not_attempted",
                "current_value": pm,
                "proposed_value": None,
                "derivation_rule": "keep_phase1b_value",
                "evidence_ref": None,
                "review_required": True,
                "visual_resolution_pending": True,
            })
        else:
            preview.append({
                "physical_page": pg,
                "is_visual_target": False,
                "visual_status": "n/a",
                "current_value": pm,
                "proposed_value": pm,
                "derivation_rule": "keep_phase1b_value",
                "evidence_ref": None,
                "review_required": False,
                "visual_resolution_pending": False,
            })

    output_path = root / PHASE1D_BASE / "fullbook_structure_merge_preview.jsonl"
    atomic_write_jsonl(output_path, preview)
    return output_path


def generate_manifest(root: Path) -> Path:
    """Generate the full-book structure scan manifest."""
    phase1c_ledger = load_call_ledger(
        root / PHASE1C_BASE / "call_ledger.json"
    )
    phase1d_ledger = load_call_ledger(
        root / PHASE1D_BASE / "call_ledger.json"
    )

    frozen = verify_frozen_files(root)

    c1c_success = sum(
        1 for pg in PHASE1C_SUCCESS_PAGES
        if phase1c_ledger.get(pg) and phase1c_ledger[pg].status == "success"
    )
    c1d_success = sum(
        1 for e in phase1d_ledger.values() if e.status == "success"
    )
    c1d_errors = sum(
        1 for e in phase1d_ledger.values()
        if e.status in ("content_error", "system_error")
    )

    manifest = {
        "phase": "phase_1d",
        "runner_version": LIVE_RUNNER_VERSION,
        "total_visual_targets": 58,
        "reused_phase_1c_successes": c1c_success,
        "reprocessed_phase_1c_failures": sum(
            1 for pg in PHASE1C_FAILURE_PAGES if pg in phase1d_ledger
        ),
        "phase_1d_successes": c1d_success,
        "phase_1d_content_errors": sum(
            1 for e in phase1d_ledger.values() if e.status == "content_error"
        ),
        "phase_1d_system_errors": sum(
            1 for e in phase1d_ledger.values() if e.status == "system_error"
        ),
        "total_visual_successes": c1c_success + c1d_success,
        "total_visual_errors": c1d_errors,
        "frozen_files_sha256": frozen,
        "formal_page_map_modified": False,
        "bridge_candidates_modified": False,
        "boundaries_modified": False,
        "api_key_logged": False,
        "data_url_persisted": False,
    }
    output_path = root / PHASE1D_BASE / "fullbook_structure_scan_manifest.json"
    atomic_write_json(output_path, manifest)
    return output_path


def verify_frozen_files(root: Path) -> dict[str, str]:
    """Return SHA-256 of frozen files."""
    files = {
        "page_map": "data/fullbook/structure/registry/page_map.jsonl",
        "bridge_candidates": "data/fullbook/structure/bridges/bridge_candidates.jsonl",
        "boundaries": "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
    }
    result: dict[str, str] = {}
    for name, rel in files.items():
        path = root / rel
        if path.is_file():
            result[name] = sha256_file(path)
        else:
            result[name] = "FILE_NOT_FOUND"
    return result


def write_run_summary(
    root: Path,
    result: PhaseDRunResult,
    *,
    prompt_sha: str,
    system_prompt_sha: str,
    schema_sha: str,
) -> Path:
    """Write the Phase 1D run summary."""
    summary = {
        "phase": "phase_1d",
        "live_runner_version": LIVE_RUNNER_VERSION,
        "total_visual_targets": 58,
        "reused_phase_1c_successes": len(PHASE1C_SUCCESS_PAGES),
        "reprocessed_phase_1c_failures": len(PHASE1C_FAILURE_PAGES),
        "recall_pages": result.recall_pages,
        "remaining_pages": result.remaining_pages,
        "recall_succeeded": result.recall_succeeded,
        "recall_content_errors": result.recall_content_errors,
        "recall_system_errors": result.recall_system_errors,
        "remaining_succeeded": result.remaining_succeeded,
        "remaining_content_errors": result.remaining_content_errors,
        "remaining_system_errors": result.remaining_system_errors,
        "total_api_calls": result.total_api_calls,
        "max_calls": PHASE1D_MAX_CALLS,
        "automatic_retries": result.automatic_retries,
        "total_prompt_tokens": result.total_prompt_tokens,
        "total_completion_tokens": result.total_completion_tokens,
        "total_tokens": result.total_tokens,
        "gate_passed": result.gate_passed,
        "gate_messages": result.gate_messages,
        "stopped_due_to_system_error": result.stopped_due_to_system_error,
        "prompt_sha256": prompt_sha,
        "system_prompt_sha256": system_prompt_sha,
        "schema_sha256": schema_sha,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "api_key_logged": False,
        "data_url_persisted": False,
        "formal_page_map_modified": False,
        "bridge_candidates_modified": False,
        "boundaries_modified": False,
    }
    output_path = root / PHASE1D_BASE / "run_summary.json"
    atomic_write_json(output_path, summary)
    return output_path
