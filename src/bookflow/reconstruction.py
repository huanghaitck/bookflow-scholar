"""Offline Phase 2B logical reconstruction and translation-context preparation."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .io_utils import atomic_write_json, atomic_write_jsonl, load_json, stable_hash
from .paths import ProjectSettings, project_root, resolve_project_path
from .phase2b_calls import (
    create_open_boundary_11_12,
    load_all_page_results,
    load_latest_boundaries,
)
from .phase2b_schemas import BoundaryDecision, LogicalBlock, ReviewItem, TranslationUnit
from .phase2b_qa import qa_disagreement_ids


class ReconstructionResult(BaseModel):
    logical_blocks_path: str
    logical_manifest_path: str
    translation_context_path: str
    review_path: str
    logical_block_count: int
    cross_page_count: int
    complete_count: int
    incomplete_start_count: int
    incomplete_end_count: int
    unresolved_count: int
    translation_ready_true: int
    translation_ready_false: int
    review_count: int


class LogicalValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    logical_block_count: int
    translation_unit_count: int
    page11_final_blocked: bool
    headers_or_page_numbers_in_body: list[str] = Field(default_factory=list)


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, first: str, second: str) -> None:
        left, right = self.find(first), self.find(second)
        if left != right:
            self.parent[right] = left


def _effective_boundaries(settings: ProjectSettings, root: Path) -> dict[str, BoundaryDecision]:
    pair = load_latest_boundaries(settings, root, "pair")
    triple = load_latest_boundaries(settings, root, "triple")
    effective = dict(pair)
    for boundary_id, decision in triple.items():
        effective[boundary_id] = decision
    open_path = (
        resolve_project_path(settings.boundary_normalized_directory, root=root)
        / "open"
        / "p0011_p0012"
        / "open_boundary.json"
    )
    if open_path.is_file():
        open_decision = BoundaryDecision.model_validate(load_json(open_path))
        effective[open_decision.boundary_id] = open_decision
    return effective


def _boundary_id(previous: int, next_page: int) -> str:
    return f"boundary_p{previous:04d}_p{next_page:04d}"


def _safe_merge(decision: BoundaryDecision | None) -> bool:
    return bool(
        decision
        and decision.paragraph_continuation is True
        and decision.structural_break == "none"
        and decision.join_operation
        in {"concatenate_without_space", "concatenate_with_space"}
        and decision.human_review_status == "not_required"
        and decision.status == "reviewed"
    )


def _join(left: str, right: str, decision: BoundaryDecision) -> str:
    if decision.join_operation == "concatenate_without_space":
        left_value = left.rstrip()
        if decision.hyphen_type == "line_break_hyphen" and left_value.endswith("-"):
            left_value = left_value[:-1]
        return left_value + right.lstrip()
    if decision.join_operation == "concatenate_with_space":
        return left.rstrip() + " " + right.lstrip()
    raise ValueError("Unsafe join operation reached logical reconstruction")


def build_logical_blocks(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    root: Path | None = None,
) -> ReconstructionResult:
    root = (root or project_root()).resolve()
    source = resolve_project_path(pdf_path, root=root)
    if source != resolve_project_path(settings.sample_pdf, root=root):
        raise PermissionError("Logical reconstruction only accepts the configured sample")
    if source == resolve_project_path(settings.source_pdf, root=root):
        raise PermissionError("The configured full PDF is prohibited")
    pages = load_all_page_results(settings, root)
    if len(pages) != 11:
        raise RuntimeError("All eleven page results are required")
    boundaries = _effective_boundaries(settings, root)
    qa_disagreements = qa_disagreement_ids(settings, root)
    if len([key for key in boundaries if not key.endswith("open")]) < 10:
        raise RuntimeError("All ten sample pair decisions are required")
    open_id = "boundary_p0011_p0012_open"
    if open_id not in boundaries:
        boundaries[open_id] = create_open_boundary_11_12(source, settings, root=root)

    allowed_types = {"body", "chapter_title", "section_title", "footnote", "caption"}
    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    first_body: dict[int, str] = {}
    last_body: dict[int, str] = {}
    for page in range(1, 12):
        body_keys: list[str] = []
        for block in sorted(pages[page].blocks, key=lambda item: item.order):
            if block.block_type not in allowed_types:
                continue
            key = f"p{page:04d}:{block.block_id}"
            nodes[key] = {
                "key": key,
                "page": page,
                "block_id": block.block_id,
                "block_type": block.block_type,
                "text": block.text,
                "uncertain": block.uncertain,
                "page_uncertain": pages[page].uncertain_characters,
                "order": block.order,
            }
            order.append(key)
            if block.block_type == "body":
                body_keys.append(key)
        if body_keys:
            first_body[page], last_body[page] = body_keys[0], body_keys[-1]

    union = _UnionFind(order)
    merged_boundary_for_pair: dict[tuple[str, str], BoundaryDecision] = {}
    for previous in range(1, 11):
        decision = boundaries.get(_boundary_id(previous, previous + 1))
        left, right = last_body.get(previous), first_body.get(previous + 1)
        if (
            left
            and right
            and decision
            and decision.boundary_id not in qa_disagreements
            and _safe_merge(decision)
        ):
            union.union(left, right)
            merged_boundary_for_pair[(left, right)] = decision

    groups: dict[str, list[str]] = defaultdict(list)
    for key in order:
        groups[union.find(key)].append(key)
    ordered_groups = sorted(groups.values(), key=lambda group: order.index(group[0]))

    logical: list[LogicalBlock] = []
    chapter_context: str | None = None
    chapter_index = 0
    for members in ordered_groups:
        members.sort(key=lambda key: order.index(key))
        first_node, last_node = nodes[members[0]], nodes[members[-1]]
        if first_node["block_type"] == "chapter_title":
            chapter_context = first_node["text"]
            chapter_index += 1
        text = first_node["text"]
        boundary_ids: list[str] = []
        merge_reasons: list[str] = []
        for left_key, right_key in zip(members, members[1:]):
            left, right = nodes[left_key], nodes[right_key]
            decision = boundaries[_boundary_id(left["page"], right["page"])]
            text = _join(text, right["text"], decision)
            boundary_ids.append(decision.boundary_id)
            merge_reasons.extend(decision.evidence)

        unresolved: list[str] = []
        incomplete_start = False
        incomplete_end = False
        human_required = False
        if first_node["block_type"] == "body" and first_body.get(first_node["page"]) == members[0]:
            if first_node["page"] > 1:
                previous_decision = boundaries.get(_boundary_id(first_node["page"] - 1, first_node["page"]))
                if previous_decision and previous_decision.paragraph_continuation is True:
                    if previous_decision.boundary_id not in boundary_ids:
                        incomplete_start = True
                        unresolved.append(previous_decision.boundary_id)
                elif previous_decision is None or previous_decision.paragraph_continuation is None:
                    incomplete_start = True
                    unresolved.append(_boundary_id(first_node["page"] - 1, first_node["page"]))
                if previous_decision and previous_decision.human_review_status in {"required", "pending"}:
                    human_required = True
        if last_node["block_type"] == "body" and last_body.get(last_node["page"]) == members[-1]:
            if last_node["page"] == 11:
                incomplete_end = True
                unresolved.append(open_id)
                human_required = True
            else:
                next_decision = boundaries.get(_boundary_id(last_node["page"], last_node["page"] + 1))
                if next_decision and next_decision.paragraph_continuation is True:
                    if next_decision.boundary_id not in boundary_ids:
                        incomplete_end = True
                        unresolved.append(next_decision.boundary_id)
                elif next_decision is None or next_decision.paragraph_continuation is None:
                    incomplete_end = True
                    unresolved.append(_boundary_id(last_node["page"], last_node["page"] + 1))
                if next_decision and next_decision.human_review_status in {"required", "pending"}:
                    human_required = True

        if incomplete_start and incomplete_end:
            completeness = "incomplete_both"
        elif incomplete_start:
            completeness = "incomplete_start"
        elif incomplete_end:
            completeness = "incomplete_end"
        elif unresolved:
            completeness = "unresolved_boundary"
        elif human_required:
            completeness = "needs_human_review"
        else:
            completeness = "complete"
        source_pages = sorted({nodes[key]["page"] for key in members})
        uncertain = sorted(
            {
                value
                for key in members
                for value in (
                    nodes[key]["page_uncertain"] if nodes[key]["uncertain"] else []
                )
            }
        )
        ready = bool(
            completeness == "complete"
            and not unresolved
            and not uncertain
            and not human_required
            and text.strip()
        )
        source_ids = [key for key in members]
        logical_id = "logical_" + stable_hash(
            {"source_block_ids": source_ids, "source_text": text, "schema": settings.logical_schema_version}
        )[:20]
        logical.append(
            LogicalBlock(
                schema_version=settings.logical_schema_version,
                logical_block_id=logical_id,
                document_id=pages[first_node["page"]].document_id,
                block_type=first_node["block_type"],
                source_pages=source_pages,
                page_start=min(source_pages),
                page_end=max(source_pages),
                cross_page=len(source_pages) > 1,
                source_block_ids=source_ids,
                source_text=text,
                boundary_ids=boundary_ids,
                word_boundary_resolved=not unresolved,
                sentence_complete=not incomplete_start and not incomplete_end and not unresolved,
                paragraph_complete=not incomplete_start and not incomplete_end and not unresolved,
                structural_context={
                    "chapter_index": chapter_index,
                    "chapter_title": chapter_context,
                },
                merge_reason=merge_reasons,
                confidence=None,
                uncertain_characters=uncertain,
                unresolved_boundaries=unresolved,
                model_review_status="needs_review" if unresolved else "completed",
                human_review_status="required" if human_required else "not_required",
                completeness_status=completeness,
                translation_ready=ready,
                created_at=datetime.now(timezone.utc),
            )
        )

    review_items = build_review_items(pages, boundaries, logical, qa_disagreements)
    translation_units = build_translation_units(logical, settings)
    logical_root = resolve_project_path(settings.logical_block_directory, root=root)
    manifest_root = resolve_project_path(settings.logical_manifest_directory, root=root)
    context_root = resolve_project_path(settings.translation_context_directory, root=root)
    review_root = resolve_project_path(settings.human_review_directory, root=root)
    logical_path = logical_root / "sample_11_pages.logical_blocks.jsonl"
    manifest_path = manifest_root / "sample_11_pages.manifest.json"
    context_path = context_root / "sample_11_pages.translation_units.jsonl"
    review_path = review_root / "sample_11_pages.review_items.jsonl"
    atomic_write_jsonl(logical_path, logical)
    atomic_write_jsonl(context_path, translation_units)
    atomic_write_jsonl(review_path, review_items)
    atomic_write_json(
        manifest_path,
        {
            "schema_version": settings.logical_schema_version,
            "document_id": pages[1].document_id,
            "source_pdf": str(source),
            "source_pages": list(range(1, 12)),
            "logical_blocks_path": str(logical_path.resolve()),
            "translation_context_path": str(context_path.resolve()),
            "review_path": str(review_path.resolve()),
            "logical_block_count": len(logical),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "translation_performed": False,
            "deepseek_calls": 0,
        },
    )
    return ReconstructionResult(
        logical_blocks_path=str(logical_path.resolve()),
        logical_manifest_path=str(manifest_path.resolve()),
        translation_context_path=str(context_path.resolve()),
        review_path=str(review_path.resolve()),
        logical_block_count=len(logical),
        cross_page_count=sum(block.cross_page for block in logical),
        complete_count=sum(block.completeness_status == "complete" for block in logical),
        incomplete_start_count=sum(block.completeness_status == "incomplete_start" for block in logical),
        incomplete_end_count=sum(block.completeness_status == "incomplete_end" for block in logical),
        unresolved_count=sum(bool(block.unresolved_boundaries) for block in logical),
        translation_ready_true=sum(block.translation_ready for block in logical),
        translation_ready_false=sum(not block.translation_ready for block in logical),
        review_count=len(review_items),
    )


def build_review_items(
    pages, boundaries, logical: list[LogicalBlock], qa_disagreements: set[str] | None = None
) -> list[ReviewItem]:
    reviews: list[ReviewItem] = []
    qa_disagreements = qa_disagreements or set()
    for decision in boundaries.values():
        qa_mismatch = decision.boundary_id in qa_disagreements
        if (
            decision.status == "reviewed"
            and decision.human_review_status == "not_required"
            and not qa_mismatch
        ):
            continue
        issue = (
            "missing_adjacent_page"
            if decision.status == "open_boundary"
            else "model_disagreement"
            if qa_mismatch
            else "uncertain_paragraph_boundary"
        )
        reviews.append(
            ReviewItem(
                review_id="review_" + stable_hash({"boundary": decision.boundary_id, "issue": issue})[:20],
                page_or_boundary=decision.boundary_id,
                source_pages=[decision.previous_page] + ([decision.next_page] if decision.next_page_available else []),
                issue_type=issue,
                relevant_block_ids=[
                    value
                    for value in [decision.previous_last_block_id, decision.next_first_block_id]
                    if value
                ],
                source_excerpt=(decision.previous_tail_text[-180:] + " | " + decision.next_head_text[:180]),
                model_decision=decision.model_dump_json(),
                conflicting_evidence=(
                    "Post-call human QA reference differs from the preserved model decision."
                    if qa_mismatch
                    else ""
                ),
                reason=decision.translation_blocked_reason or "; ".join(decision.evidence),
                suggested_action=(
                    "Provide the missing adjacent sample page in a separately approved phase."
                    if decision.status == "open_boundary"
                    else "Review the preserved images and model response; do not auto-merge or overwrite the model record."
                ),
                blocking_translation=True,
            )
        )
    for block in logical:
        if block.completeness_status in {"incomplete_start", "incomplete_both"}:
            issue = "incomplete_start"
        elif block.completeness_status in {"incomplete_end", "incomplete_both"}:
            issue = "incomplete_end"
        else:
            continue
        reviews.append(
            ReviewItem(
                review_id="review_" + stable_hash({"logical": block.logical_block_id, "issue": issue})[:20],
                page_or_boundary=block.logical_block_id,
                source_pages=block.source_pages,
                issue_type=issue,
                relevant_block_ids=block.source_block_ids,
                source_excerpt=block.source_text[:360],
                model_decision=block.completeness_status,
                conflicting_evidence="",
                reason="The logical paragraph is not complete inside the approved sample range.",
                suggested_action="Keep translation_ready=false until the missing boundary is resolved.",
                blocking_translation=True,
            )
        )
    return reviews


def build_translation_units(
    logical: list[LogicalBlock], settings: ProjectSettings
) -> list[TranslationUnit]:
    units: list[TranslationUnit] = []
    body_indices = [index for index, block in enumerate(logical) if block.block_type == "body"]
    for index in body_indices:
        block = logical[index]
        chapter = block.structural_context.get("chapter_title")
        same_chapter = lambda candidate: candidate.structural_context.get("chapter_index") == block.structural_context.get("chapter_index")
        before = next(
            (
                logical[candidate]
                for candidate in reversed(body_indices)
                if candidate < index
                and logical[candidate].completeness_status == "complete"
                and same_chapter(logical[candidate])
            ),
            None,
        )
        after = next(
            (
                logical[candidate]
                for candidate in body_indices
                if candidate > index
                and logical[candidate].completeness_status == "complete"
                and same_chapter(logical[candidate])
            ),
            None,
        )
        context_required = bool(
            re.match(
                r"^\s*(and|but|yet|so|then|he|she|it|they|this|that|these|those|such|his|her|their)\b",
                block.source_text,
                flags=re.IGNORECASE,
            )
        )
        context_complete = bool(before or after) or not context_required
        ready = block.translation_ready and context_complete
        if not block.translation_ready:
            blocked = f"Logical block is {block.completeness_status}."
        elif not context_complete:
            blocked = "Required translation context is unavailable or incomplete."
        else:
            blocked = None
        units.append(
            TranslationUnit(
                schema_version=settings.translation_context_schema_version,
                translation_unit_id="translation_" + stable_hash(
                    {"logical_block_id": block.logical_block_id, "schema": settings.translation_context_schema_version}
                )[:20],
                target_logical_block_id=block.logical_block_id,
                source_text=block.source_text,
                context_before_block_ids=[before.logical_block_id] if before else [],
                context_after_block_ids=[after.logical_block_id] if after else [],
                context_before_text=before.source_text if before else "",
                context_after_text=after.source_text if after else "",
                chapter_context=str(chapter) if chapter else None,
                translate_target_only=True,
                context_complete=context_complete,
                context_required=context_required,
                translation_ready=ready,
                blocked_reason=blocked,
            )
        )
    return units


def validate_logical_outputs(
    logical_path: str | Path,
    translation_context_path: str | Path,
) -> LogicalValidation:
    logical = [
        LogicalBlock.model_validate(json.loads(line))
        for line in Path(logical_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    units = [
        TranslationUnit.model_validate(json.loads(line))
        for line in Path(translation_context_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    errors: list[str] = []
    ids = {block.logical_block_id for block in logical}
    if len(ids) != len(logical):
        errors.append("Duplicate logical_block_id")
    if any(unit.target_logical_block_id not in ids for unit in units):
        errors.append("Translation unit target is not traceable")
    final = [block for block in logical if 11 in block.source_pages and block.block_type == "body"]
    final_blocked = bool(
        final
        and final[-1].translation_ready is False
        and final[-1].sentence_complete is False
        and final[-1].paragraph_complete is False
        and final[-1].completeness_status == "incomplete_end"
        and "boundary_p0011_p0012_open" in final[-1].unresolved_boundaries
    )
    if not final_blocked:
        errors.append("The final page 11 paragraph is not safely blocked")
    suspicious = [
        block.logical_block_id
        for block in logical
        if block.block_type == "body"
        and re.search(r"(^|\n)\s*(THE [A-Z ]+|\d{1,3})\s*(\n|$)", block.source_text)
    ]
    if suspicious:
        errors.append("Possible header or page number in body")
    return LogicalValidation(
        valid=not errors,
        errors=errors,
        logical_block_count=len(logical),
        translation_unit_count=len(units),
        page11_final_blocked=final_blocked,
        headers_or_page_numbers_in_body=suspicious,
    )
