"""Offline request builder for Phase 1C-B visual classification.

Pure functions that read Phase 1C-A target data and sample batch, then build
deterministic ``VisualPageClassificationRequest`` records without touching
images, API keys, or the network.
"""

from __future__ import annotations

import json
from pathlib import Path

from .io_utils import atomic_write_jsonl, stable_hash
from .structure_visual_schemas import (
    VisualBatchRequestManifest,
    VisualPageClassificationRequest,
    VisualRequestContext,
)

SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "v1"

# Context summary: cap evidence lines to avoid transmitting large text.
_MAX_EVIDENCE_LINES = 5
_MAX_NEIGHBOR_ROLES = 4


def _load_targets(root: Path) -> list[dict]:
    path = root / "data/fullbook/structure/phase1c/ambiguous_page_targets.jsonl"
    targets: list[dict] = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            targets.append(json.loads(line))
    return targets


def _load_sample_batch(root: Path) -> dict:
    path = root / "data/fullbook/structure/phase1c/sample_batch_v1.json"
    return json.loads(path.read_text("utf-8"))


def _load_page_map(root: Path) -> dict[int, dict]:
    path = root / "data/fullbook/structure/registry/page_map.jsonl"
    records: dict[int, dict] = {}
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            records[d["physical_page"]] = d
    return records


def _compute_request_fingerprint(
    physical_page: int,
    page_image_sha256: str,
    schema_version: str,
    prompt_version: str,
    context_json: str,
) -> str:
    return stable_hash({
        "physical_page": physical_page,
        "page_image_sha256": page_image_sha256,
        "schema_version": schema_version,
        "prompt_version": prompt_version,
        "context_json": context_json,
    })


def build_visual_page_request(
    target: dict,
    *,
    page_map_record: dict | None = None,
    schema_version: str = SCHEMA_VERSION,
    prompt_version: str = PROMPT_VERSION,
) -> VisualPageClassificationRequest:
    """Build a single visual classification request from a target record."""
    physical_page = target["physical_page"]

    neighboring_prose = target.get("neighboring_prose_pages", [])
    neighboring_roles: list[str] = []
    if page_map_record and neighboring_prose:
        for np in neighboring_prose:
            rec = page_map_record.get(np)
            if rec:
                neighboring_roles.append(rec.get("primary_role", "unknown"))
            if len(neighboring_roles) >= _MAX_NEIGHBOR_ROLES:
                break

    evidence_summary = target.get("current_evidence", [])[:_MAX_EVIDENCE_LINES]

    context = VisualRequestContext(
        physical_page=physical_page,
        source_page_asset_ref=target["source_page_asset_ref"],
        page_image_sha256=target["page_image_sha256"],
        pdf_text_length=target.get("pdf_text_length", 0),
        pdf_word_count=target.get("pdf_word_count", 0),
        embedded_image_count=target.get("embedded_image_count", 0),
        ink_coverage=target.get("ink_coverage", 0.0),
        edge_density=target.get("edge_density", 0.0),
        current_primary_role=target.get("current_primary_role", "unknown"),
        current_content_features=target.get("current_content_features", []),
        current_classification_source=target.get("current_classification_source", ""),
        current_evidence_summary=evidence_summary,
        neighboring_prose_pages=neighboring_prose,
        neighboring_primary_roles=neighboring_roles,
        target_group=target.get("target_group", "other_unknown"),
        requested_visual_questions=target.get("requested_visual_questions", []),
    )

    context_json = context.model_dump_json()
    fingerprint = _compute_request_fingerprint(
        physical_page,
        target["page_image_sha256"],
        schema_version,
        prompt_version,
        context_json,
    )

    return VisualPageClassificationRequest(
        request_id=f"visreq_p{physical_page:04d}",
        schema_version=schema_version,
        prompt_version=prompt_version,
        physical_page=physical_page,
        context=context,
        request_fingerprint=fingerprint,
    )


def build_sample_batch_requests(
    root: Path,
    *,
    schema_version: str = SCHEMA_VERSION,
    prompt_version: str = PROMPT_VERSION,
) -> list[VisualPageClassificationRequest]:
    """Build requests for all pages in the Phase 1C-A sample batch."""
    sample = _load_sample_batch(root)
    selected_pages = sample["selected_pages"]

    targets = _load_targets(root)
    target_map = {t["physical_page"]: t for t in targets}

    page_map = _load_page_map(root)

    requests: list[VisualPageClassificationRequest] = []
    for pg in selected_pages:
        target = target_map.get(pg)
        if target is None:
            raise ValueError(
                f"Sample page {pg} not found in ambiguous_page_targets.jsonl"
            )
        req = build_visual_page_request(
            target,
            page_map_record=page_map,
            schema_version=schema_version,
            prompt_version=prompt_version,
        )
        requests.append(req)

    return requests


def validate_request_asset_refs(
    requests: list[VisualPageClassificationRequest],
) -> list[str]:
    """Return a list of violations (empty if all refs are project-relative)."""
    violations: list[str] = []
    for req in requests:
        ref = req.context.source_page_asset_ref
        if not ref:
            violations.append(f"p{req.physical_page}: empty asset ref")
        elif len(ref) >= 2 and ref[1] == ":":
            violations.append(f"p{req.physical_page}: absolute path with drive letter")
        elif ref.startswith("/") or ref.startswith("\\"):
            violations.append(f"p{req.physical_page}: absolute path with leading slash")
    return violations


def write_sample_batch_requests(
    root: Path,
    output_path: str | Path | None = None,
) -> Path:
    """Build and write the sample batch requests JSONL. Returns the output path."""
    if output_path is None:
        output_path = (
            root
            / "data/fullbook/structure/phase1c/sample_batch_v1_requests.jsonl"
        )
    output_path = Path(output_path)

    requests = build_sample_batch_requests(root)
    violations = validate_request_asset_refs(requests)
    if violations:
        raise ValueError(f"Asset ref violations: {violations}")

    atomic_write_jsonl(output_path, requests)
    return output_path
