"""Non-authoritative, offline Mock provider for downstream pipeline tests."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz
from pydantic import BaseModel, Field

from .io_utils import atomic_write_json, atomic_write_jsonl, load_json, stable_hash
from .page_pipeline import build_context, validate_manifest_file
from .paths import ProjectSettings, project_root, resolve_project_path
from .schemas import (
    ContinuityCandidate,
    PageManifest,
    PageRecord,
    SCHEMA_VERSION,
    VisionBlock,
    VisionPageResult,
)


class MockVisionRunResult(BaseModel):
    document_id: str
    source_pdf: str
    selected_pages: list[int]
    generated_pages: list[int] = Field(default_factory=list)
    cached_pages: list[int] = Field(default_factory=list)
    failed_pages: list[int] = Field(default_factory=list)
    needs_real_vision_pages: list[int] = Field(default_factory=list)
    errors: dict[int, str] = Field(default_factory=dict)
    raw_directory: str
    normalized_directory: str
    continuity_path: str
    continuity_candidate_count: int
    human_review_candidate_count: int
    elapsed_seconds: float
    provider: str = "mock"
    authoritative: bool = False
    api_called: bool = False
    offline: bool = True


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    parts = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    return parts or [normalized]


def _mock_paths(
    settings: ProjectSettings,
    root: Path,
    slug: str,
    profile_id: str,
    page_number: int,
) -> tuple[Path, Path, Path]:
    base = resolve_project_path(settings.mock_vision_directory, root=root)
    profile = base / slug / f"profile_{profile_id}"
    name = f"page_{page_number:04d}.json"
    return profile / "raw" / name, profile / "normalized" / name, profile


def _body_blocks(document_id: str, page_number: int, text: str) -> list[VisionBlock]:
    blocks: list[VisionBlock] = []
    for order, paragraph in enumerate(_paragraphs(text), start=1):
        block_id = "blk_" + stable_hash(
            {
                "document_id": document_id,
                "pdf_page": page_number,
                "order": order,
                "text": paragraph,
                "provider": "mock",
            }
        )[:20]
        blocks.append(
            VisionBlock(
                block_id=block_id,
                block_type="body",
                order=order,
                text=paragraph,
                bounding_box=None,
                confidence=None,
                uncertain=False,
                notes="Mock block from embedded PDF text layer; not a visual finding.",
            )
        )
    return blocks


def _first_body(result: VisionPageResult) -> VisionBlock | None:
    return next((block for block in result.blocks if block.block_type == "body"), None)


def _last_body(result: VisionPageResult) -> VisionBlock | None:
    return next(
        (block for block in reversed(result.blocks) if block.block_type == "body"), None
    )


def _candidate(
    previous: VisionPageResult,
    following: VisionPageResult,
) -> ContinuityCandidate:
    previous_block = _last_body(previous)
    next_block = _first_body(following)
    previous_text = previous_block.text.strip() if previous_block else ""
    next_text = next_block.text.strip() if next_block else ""
    tail = previous_text[-240:]
    head = next_text[:240]
    next_first_character = next((char for char in next_text if char.isalpha()), "")
    possible_word_break = previous_text.endswith("-") and bool(next_first_character)
    ending = previous_text.rstrip().rstrip('"\'”’)]}')
    possible_sentence = bool(ending) and ending[-1] not in ".!?;:"
    next_lowercase = bool(next_first_character and next_first_character.islower())
    possible_paragraph = bool(
        previous_block and next_block and possible_sentence and next_lowercase
    )
    signals: list[str] = []
    if possible_word_break:
        signals.append("previous_page_ends_with_hyphen")
    if possible_sentence:
        signals.append("previous_page_lacks_terminal_punctuation")
    if next_lowercase:
        signals.append("next_page_first_letter_is_lowercase")
    if next_block:
        signals.append("next_page_first_available_block_is_body")
    if previous.page_type == "unknown" and following.page_type == "unknown":
        signals.append("mock_has_no_authoritative_structural_break")
    candidate_id = "continuity_" + stable_hash(
        {
            "document_id": previous.document_id,
            "previous_page": previous.pdf_page,
            "next_page": following.pdf_page,
            "previous_block": previous_block.block_id if previous_block else None,
            "next_block": next_block.block_id if next_block else None,
        }
    )[:20]
    return ContinuityCandidate(
        candidate_id=candidate_id,
        document_id=previous.document_id,
        previous_page=previous.pdf_page,
        next_page=following.pdf_page,
        previous_last_block_id=previous_block.block_id if previous_block else None,
        next_first_block_id=next_block.block_id if next_block else None,
        previous_tail_text=tail,
        next_head_text=head,
        possible_word_break=possible_word_break,
        possible_sentence_continuation=possible_sentence,
        possible_paragraph_continuation=possible_paragraph,
        rule_signals=signals,
        model_review_required=True,
        human_review_required=True,
        decision="pending",
        merge_text="",
        status="candidate_only",
    )


def _write_mock_log(settings: ProjectSettings, root: Path, result: MockVisionRunResult) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = resolve_project_path(settings.log_directory, root=root) / f"mock_{stamp}.json"
    atomic_write_json(
        path,
        {
            "run_id": str(uuid.uuid4()),
            "operation": "mock_vision",
            "result": result.model_dump(mode="json"),
        },
    )


def run_mock_vision(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    pages: str | list[int] | None = None,
    dpi: int | None = None,
    color_mode: str | None = None,
    image_format: str | None = None,
    root: Path | None = None,
    image_root: Path | None = None,
    manifest_root: Path | None = None,
    cache_root: Path | None = None,
) -> MockVisionRunResult:
    """Create Mock-only raw and normalized records from embedded PDF text."""

    started = time.perf_counter()
    root = (root or project_root()).resolve()
    context = build_context(
        pdf_path,
        settings,
        pages=pages,
        dpi=dpi,
        color_mode=color_mode,
        image_format=image_format,
        root=root,
        image_root=image_root,
        manifest_root=manifest_root,
        cache_root=cache_root,
    )
    validation = validate_manifest_file(context.manifest_path, source_pdf=context.source_pdf)
    if not validation.ready_for_vision:
        raise RuntimeError("Rendered pages failed validation; Mock vision was not started")
    manifest = PageManifest.model_validate(load_json(context.manifest_path))
    records_by_page: dict[int, PageRecord] = {}
    for path_value in manifest.page_record_paths:
        record = PageRecord.model_validate(load_json(path_value))
        records_by_page[record.pdf_page] = record

    generated: list[int] = []
    cached: list[int] = []
    failed: list[int] = []
    needs_real: list[int] = []
    errors: dict[int, str] = {}
    normalized_results: list[VisionPageResult] = []
    profile_dir: Path | None = None
    with fitz.open(context.source_pdf) as document:
        for page_number in context.pages:
            record = records_by_page[page_number]
            raw_path, normalized_path, profile_dir = _mock_paths(
                settings, root, context.document_slug, context.render_profile_id, page_number
            )
            try:
                text = document.load_page(page_number - 1).get_text("text") or ""
                fingerprint = stable_hash(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "provider": "mock",
                        "source_method": "pdf_text_layer",
                        "source_image_sha256": record.image_sha256,
                        "text": text,
                    }
                )
                if raw_path.exists() and normalized_path.exists():
                    existing = VisionPageResult.model_validate(load_json(normalized_path))
                    raw_existing = load_json(raw_path)
                    if (
                        existing.input_fingerprint == fingerprint
                        and existing.provider == "mock"
                        and existing.authoritative is False
                        and existing.api_called is False
                        and raw_existing.get("input_fingerprint") == fingerprint
                    ):
                        cached.append(page_number)
                        normalized_results.append(existing)
                        if existing.status == "needs_real_vision":
                            needs_real.append(page_number)
                        continue
                if raw_path.exists():
                    raw = load_json(raw_path)
                    if raw.get("input_fingerprint") != fingerprint:
                        raise RuntimeError(
                            "Existing Mock raw response has a different input fingerprint"
                        )
                    text = str(raw.get("extracted_text", ""))
                else:
                    atomic_write_json(
                        raw_path,
                        {
                            "schema_version": SCHEMA_VERSION,
                            "response_type": "mock_pdf_text_layer",
                            "document_id": context.document_id,
                            "pdf_page": page_number,
                            "provider": "mock",
                            "model": None,
                            "source_method": "pdf_text_layer",
                            "source_image": record.image_path,
                            "source_image_sha256": record.image_sha256,
                            "input_fingerprint": fingerprint,
                            "extracted_text": text,
                            "authoritative": False,
                            "api_called": False,
                            "created_at": _utc_iso(),
                            "warning": "Mock data only; not OCR and not a GLM response.",
                        },
                    )
                blocks = _body_blocks(context.document_id, page_number, text)
                warnings = [
                    "Mock data from embedded PDF text layer; not authoritative visual transcription."
                ]
                if not blocks:
                    warnings.append("No embedded text; real visual review will be required.")
                normalized = VisionPageResult(
                    document_id=context.document_id,
                    pdf_page=page_number,
                    provider="mock",
                    model=None,
                    source_method="pdf_text_layer",
                    source_image=record.image_path,
                    source_image_sha256=record.image_sha256,
                    page_type="unknown",
                    printed_page=None,
                    title=None,
                    running_header=None,
                    footer=None,
                    page_number_text=None,
                    blocks=blocks,
                    continuation_from_previous=None,
                    continuation_to_next=None,
                    uncertain_characters=[],
                    warnings=warnings,
                    raw_response_path=str(raw_path.resolve()),
                    normalized_output_path=str(normalized_path.resolve()),
                    input_fingerprint=fingerprint,
                    status="mock_completed" if blocks else "needs_real_vision",
                    authoritative=False,
                    api_called=False,
                )
                atomic_write_json(normalized_path, normalized)
                generated.append(page_number)
                normalized_results.append(normalized)
                if not blocks:
                    needs_real.append(page_number)
            except Exception as exc:
                failed.append(page_number)
                errors[page_number] = f"{type(exc).__name__}: {exc}"

    if failed:
        continuity: list[ContinuityCandidate] = []
    else:
        ordered = sorted(normalized_results, key=lambda item: item.pdf_page)
        continuity = [
            _candidate(previous, following)
            for previous, following in zip(ordered, ordered[1:])
            if following.pdf_page == previous.pdf_page + 1
        ]
    continuity_root = (
        resolve_project_path(settings.continuity_directory, root=root)
        / context.document_slug
        / f"profile_{context.render_profile_id}"
    )
    continuity_path = continuity_root / "candidates.jsonl"
    for candidate in continuity:
        atomic_write_json(
            continuity_root
            / "candidates"
            / f"page_{candidate.previous_page:04d}_to_page_{candidate.next_page:04d}.json",
            candidate,
        )
    atomic_write_jsonl(continuity_path, continuity)
    profile_dir = profile_dir or resolve_project_path(
        settings.mock_vision_directory, root=root
    ) / context.document_slug / f"profile_{context.render_profile_id}"
    result = MockVisionRunResult(
        document_id=context.document_id,
        source_pdf=context.source_pdf,
        selected_pages=context.pages,
        generated_pages=generated,
        cached_pages=cached,
        failed_pages=failed,
        needs_real_vision_pages=needs_real,
        errors=errors,
        raw_directory=str((profile_dir / "raw").resolve()),
        normalized_directory=str((profile_dir / "normalized").resolve()),
        continuity_path=str(continuity_path.resolve()),
        continuity_candidate_count=len(continuity),
        human_review_candidate_count=sum(
            1 for candidate in continuity if candidate.human_review_required
        ),
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )
    _write_mock_log(settings, root, result)
    return result
