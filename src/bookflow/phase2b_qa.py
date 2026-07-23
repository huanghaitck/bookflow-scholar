"""Post-call offline QA comparison; reference answers are never model inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .io_utils import atomic_write_json
from .paths import ProjectSettings, project_root, resolve_project_path
from .phase2b_calls import load_latest_boundaries
from .phase2b_schemas import BoundaryDecision


class BoundaryQAItem(BaseModel):
    boundary_id: str
    expected: dict[str, Any]
    observed: dict[str, Any] | None
    matched: bool
    differing_fields: list[str] = Field(default_factory=list)
    action: str


class BoundaryQAReport(BaseModel):
    schema_version: str = "1.0"
    comparison_mode: str = "post_call_offline_qa_only"
    reference_sent_to_model: bool = False
    model_results_overwritten: bool = False
    compared: int
    matched: int
    mismatched: int
    missing: int
    human_review_required: int
    items: list[BoundaryQAItem]
    created_at: datetime


def run_offline_boundary_qa(
    settings: ProjectSettings, *, root: Path | None = None
) -> BoundaryQAReport:
    root = (root or project_root()).resolve()
    reference_path = resolve_project_path(settings.boundary_qa_reference_path, root=root)
    reference = yaml.safe_load(reference_path.read_text(encoding="utf-8"))
    if reference.get("usage") != "post_call_offline_qa_only" or not reference.get("must_not_be_sent_to_model"):
        raise ValueError("QA reference is not marked post-call/offline-only")
    observed: dict[str, BoundaryDecision] = load_latest_boundaries(settings, root, "pair")
    observed.update(load_latest_boundaries(settings, root, "triple"))
    items: list[BoundaryQAItem] = []
    for label, expected in reference["boundaries"].items():
        previous, next_page = (int(value) for value in label.split("-"))
        boundary_id = f"boundary_p{previous:04d}_p{next_page:04d}"
        decision = observed.get(boundary_id)
        actual = None
        differing: list[str] = []
        if decision is not None:
            actual = {
                "word_continuation": decision.word_continuation,
                "sentence_continuation": decision.sentence_continuation,
                "paragraph_continuation": decision.paragraph_continuation,
                "structural_break": decision.structural_break,
                "join_operation": decision.join_operation,
            }
            differing = [name for name, value in expected.items() if actual.get(name) != value]
        matched = decision is not None and not differing
        items.append(
            BoundaryQAItem(
                boundary_id=boundary_id,
                expected=expected,
                observed=actual,
                matched=matched,
                differing_fields=differing if decision is not None else ["missing_model_result"],
                action="none" if matched else "human_review_without_overwriting_model_result",
            )
        )
    report = BoundaryQAReport(
        compared=len(items),
        matched=sum(item.matched for item in items),
        mismatched=sum(item.observed is not None and not item.matched for item in items),
        missing=sum(item.observed is None for item in items),
        human_review_required=sum(not item.matched for item in items),
        items=items,
        created_at=datetime.now(timezone.utc),
    )
    atomic_write_json(resolve_project_path(settings.boundary_qa_output_path, root=root), report)
    return report


def qa_disagreement_ids(settings: ProjectSettings, root: Path) -> set[str]:
    path = resolve_project_path(settings.boundary_qa_output_path, root=root)
    if not path.is_file():
        return set()
    report = BoundaryQAReport.model_validate_json(path.read_text(encoding="utf-8"))
    return {item.boundary_id for item in report.items if not item.matched}
