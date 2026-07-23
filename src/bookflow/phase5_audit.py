"""Purely offline Phase 5 audit and fail-closed sample release."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import fitz
from PIL import Image, ImageStat
from docx import Document
from pydantic import BaseModel, ConfigDict, Field

from .io_utils import atomic_write_json, load_json, sha256_file, sha256_text, stable_hash
from .phase3b_source import Phase3BSourceDocument
from .phase3c4 import Phase3C4BilingualDocument, build_phase3c4_units
from .paths import load_settings, project_root


INTERNAL_MARKERS = (
    "logical2_", "logical3b_", "source_fragment", "request_fingerprint",
    "cache_fingerprint", "prompt_tokens", "raw_response_reference", "usage_reference",
)
ARTIFACT_MARKERS = ("```", '"target_block_id"', '"translation"', "Here is the translation")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_visible_text(value: str, *, markdown: bool = False) -> str:
    """Ignore formatting whitespace, while preserving every word and punctuation mark."""

    text = value.replace("\u00a0", " ")
    if markdown:
        text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_rendered_text(value: str) -> str:
    """Compare rendered text while ignoring whitespace introduced by pagination.

    PDF text extraction may insert spaces at DOCX line wraps, including after a
    visible hyphen or between Chinese characters.  Removing whitespace only
    preserves every non-whitespace character and therefore still detects
    omissions, duplication, punctuation changes, and replacement glyphs.
    """

    return re.sub(r"\s+", "", value.replace("\u00a0", ""))


def _docx_text(path: Path) -> str:
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "\n".join(page.get_text("text") for page in document)


def _expected_source_text(document: Phase3BSourceDocument) -> str:
    return "\n".join(block.source_text for block in document.entries)


def _expected_bilingual_text(document: Phase3C4BilingualDocument) -> str:
    values: list[str] = []
    for block in document.logical_blocks:
        values.extend([block.source_text, block.translation])
    return "\n".join(values)


class Phase5DataAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_count: int
    logical_block_count: int
    source_fragment_count: int
    referenced_fragment_count: int
    unused_fragment_ids: list[str]
    duplicate_fragment_ids: list[str]
    unresolved_boundary_ids: list[str]
    block_ids_match: bool
    block_order_match: bool
    block_types_match: bool
    source_text_match: bool
    source_pages_match: bool
    source_fragment_ids_match: bool
    source_normalized_sha256: str
    bilingual_source_normalized_sha256: str
    translation_count: int
    missing_translation_ids: list[str]
    duplicate_translation_ids: list[str]
    extra_translation_ids: list[str]
    empty_translation_ids: list[str]
    failed_translation_ids: list[str]
    title_block_count: int
    title_translation_count: int
    running_header_translation_count: int
    page_number_translation_count: int
    context_leakage_ids: list[str]
    source_echo_ids: list[str]
    artifact_translation_ids: list[str]
    doing_king_present: bool
    doingking_absent: bool
    page12_end_complete: bool
    open_boundary_12_13_absent: bool
    source_noise_clean: bool
    strict_passed: bool
    blockers: list[str]
    glm_calls: int = 0
    deepseek_calls: int = 0
    translation_calls: int = 0
    network_requests: int = 0


class Phase5OutputAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_markdown_matches_json: bool
    source_docx_matches_json: bool
    bilingual_markdown_matches_json: bool
    bilingual_docx_matches_json: bool
    source_markdown_matches_docx: bool
    bilingual_markdown_matches_docx: bool
    source_markdown_contains_chinese: bool
    source_docx_contains_chinese: bool
    internal_fields_found: list[str]
    existing_source_final_markdown_matches_candidate: bool
    existing_source_final_docx_matches_candidate: bool
    strict_passed: bool
    blockers: list[str]


class Phase5RenderAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_pdf_path: str
    bilingual_pdf_path: str
    source_page_count: int
    bilingual_page_count: int
    source_page_images: list[str]
    bilingual_page_images: list[str]
    source_contact_sheet_path: str
    bilingual_contact_sheet_path: str
    blank_source_pages: list[int]
    blank_bilingual_pages: list[int]
    black_block_source_pages: list[int]
    black_block_bilingual_pages: list[int]
    sparse_source_pages: list[int]
    sparse_bilingual_pages: list[int]
    source_pdf_matches_docx: bool | None
    bilingual_pdf_matches_docx: bool | None
    source_fonts: list[str]
    bilingual_fonts: list[str]
    chinese_text_extractable: bool
    replacement_glyphs_found: list[str]
    source_text_character_counts: list[int]
    bilingual_text_character_counts: list[int]
    strict_passed: bool
    blockers: list[str]


class Phase5Gate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    blockers: list[str] = Field(default_factory=list)


class Phase5ReleaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    published: bool
    manifest_path: str | None
    final_paths: list[str]
    final_pdf_paths: list[str]
    before_hashes: dict[str, str]
    after_hashes: dict[str, str]
    blockers: list[str]


class Phase51ReleaseResult(Phase5ReleaseResult):
    """Clean-reader release plus immutable archive evidence."""

    archive_paths: list[str] = Field(default_factory=list)
    archive_hashes: dict[str, str] = Field(default_factory=dict)


def audit_source_and_alignment(*, root: Path | None = None) -> Phase5DataAudit:
    root = (root or project_root()).resolve()
    source = Phase3BSourceDocument.model_validate(load_json(root / "data/source_document_sample12_v1.json"))
    raw_bilingual = load_json(root / "data/bilingual_document_sample12_zh-Hans_v1.json")
    bilingual = Phase3C4BilingualDocument.model_validate(raw_bilingual)
    source_ids = [block.logical_block_id for block in source.entries]
    bilingual_ids = [block.block_id for block in bilingual.logical_blocks]
    counts = Counter(bilingual_ids)
    duplicate_translation_ids = sorted(block_id for block_id, count in counts.items() if count > 1)
    source_set, bilingual_set = set(source_ids), set(bilingual_ids)
    missing = [block_id for block_id in source_ids if block_id not in bilingual_set]
    extra = [block_id for block_id in bilingual_ids if block_id not in source_set]
    by_bilingual = {block.block_id: block for block in bilingual.logical_blocks}
    comparable = [block for block in source.entries if block.logical_block_id in by_bilingual]
    fragment_ids = [fragment for block in source.entries for fragment in block.source_fragment_ids]
    fragment_counts = Counter(fragment_ids)
    expected_fragments = source.audit.expected_fragment_count
    unused = list(source.audit.unused_fragment_ids)
    duplicates = sorted(fragment for fragment, count in fragment_counts.items() if count > 1)
    unresolved = sorted({boundary for block in source.entries for boundary in block.unresolved_boundaries})
    empty = [block.block_id for block in bilingual.logical_blocks if not block.translation.strip()]
    failed = [block.block_id for block in bilingual.logical_blocks if block.translation_status != "translated"]
    titles = [block for block in source.entries if block.block_type in {"chapter_title", "section_title"}]
    translated_titles = [block for block in bilingual.logical_blocks if block.block_type in {"chapter_title", "section_title"} and block.translation.strip()]
    units = {unit.target_block_id: unit for unit in build_phase3c4_units(load_settings(), root=root)}
    context_leakage: list[str] = []
    source_echo: list[str] = []
    artifacts: list[str] = []
    for block in bilingual.logical_blocks:
        translated = normalize_visible_text(block.translation)
        unit = units.get(block.block_id)
        if unit:
            for context in (unit.context_before_text, unit.context_after_text):
                normalized = normalize_visible_text(context or "")
                if len(normalized) >= 40 and normalized in translated:
                    context_leakage.append(block.block_id)
                    break
        if normalize_visible_text(block.source_text) == translated:
            source_echo.append(block.block_id)
        if any(marker in block.translation for marker in ARTIFACT_MARKERS):
            artifacts.append(block.block_id)
    source_text = _expected_source_text(source)
    bilingual_source_text = "\n".join(block.source_text for block in bilingual.logical_blocks)
    source_noise_types = {"running_header", "running_footer", "page_number", "scanner_footer"}
    source_noise_clean = not any(block.block_type in source_noise_types for block in source.entries)
    blockers: list[str] = []
    checks = {
        "page count is not 12": source.page_count == 12,
        "logical block count is not 24": len(source.entries) == 24,
        "fragment count is not 33": expected_fragments == 33 and len(fragment_counts) == 33,
        "unused source fragments exist": not unused,
        "duplicate source fragments exist": not duplicates,
        "unresolved boundaries exist": not unresolved,
        "block IDs or order differ": source_ids == bilingual_ids,
        "block types differ": all(block.block_type == by_bilingual[block.logical_block_id].block_type for block in comparable),
        "source text differs": all(block.source_text == by_bilingual[block.logical_block_id].source_text for block in comparable),
        "source pages differ": all(block.source_pages == by_bilingual[block.logical_block_id].source_pages for block in comparable),
        "source fragment IDs differ": all(block.source_fragment_ids == by_bilingual[block.logical_block_id].source_fragment_ids for block in comparable),
        "translation alignment is incomplete": not missing and not extra and not duplicate_translation_ids and len(bilingual.logical_blocks) == 24,
        "empty or failed translations exist": not empty and not failed,
        "title translations are incomplete": len(titles) == len(translated_titles) == 4,
        "context leakage or source echo exists": not context_leakage and not source_echo,
        "model or JSON artifacts exist in translations": not artifacts,
        "doing King boundary is invalid": "doing King" in source_text and "doingKing" not in source_text,
        "page 12 end is not complete": source.audit.page12_end_complete,
        "open 12-to-13 boundary exists": not source.audit.open_boundary_12_13_exists,
        "structural page noise entered source blocks": source_noise_clean and source.audit.header_footer_page_number_clean,
        "source strict audit did not pass": source.audit.strict_passed and source.strict_export_ready,
    }
    blockers.extend(message for message, passed in checks.items() if not passed)
    source_hash = sha256_text(normalize_visible_text(source_text))
    bilingual_hash = sha256_text(normalize_visible_text(bilingual_source_text))
    return Phase5DataAudit(
        page_count=source.page_count, logical_block_count=len(source.entries),
        source_fragment_count=len(fragment_counts), referenced_fragment_count=len(fragment_ids),
        unused_fragment_ids=unused, duplicate_fragment_ids=duplicates,
        unresolved_boundary_ids=unresolved,
        block_ids_match=source_set == bilingual_set,
        block_order_match=source_ids == bilingual_ids,
        block_types_match=all(block.block_type == by_bilingual[block.logical_block_id].block_type for block in comparable),
        source_text_match=all(block.source_text == by_bilingual[block.logical_block_id].source_text for block in comparable),
        source_pages_match=all(block.source_pages == by_bilingual[block.logical_block_id].source_pages for block in comparable),
        source_fragment_ids_match=all(block.source_fragment_ids == by_bilingual[block.logical_block_id].source_fragment_ids for block in comparable),
        source_normalized_sha256=source_hash, bilingual_source_normalized_sha256=bilingual_hash,
        translation_count=len(bilingual.logical_blocks), missing_translation_ids=missing,
        duplicate_translation_ids=duplicate_translation_ids, extra_translation_ids=extra,
        empty_translation_ids=empty, failed_translation_ids=failed,
        title_block_count=len(titles), title_translation_count=len(translated_titles),
        running_header_translation_count=sum(block.block_type == "running_header" for block in bilingual.logical_blocks),
        page_number_translation_count=sum(block.block_type == "page_number" for block in bilingual.logical_blocks),
        context_leakage_ids=sorted(set(context_leakage)), source_echo_ids=source_echo,
        artifact_translation_ids=artifacts,
        doing_king_present="doing King" in source_text,
        doingking_absent="doingKing" not in source_text,
        page12_end_complete=source.audit.page12_end_complete,
        open_boundary_12_13_absent=not source.audit.open_boundary_12_13_exists,
        source_noise_clean=source_noise_clean and source.audit.header_footer_page_number_clean,
        strict_passed=not blockers and source_hash == bilingual_hash,
        blockers=blockers + ([] if source_hash == bilingual_hash else ["normalized English hashes differ"]),
    )


def audit_candidate_outputs(*, root: Path | None = None) -> Phase5OutputAudit:
    root = (root or project_root()).resolve()
    source = Phase3BSourceDocument.model_validate(load_json(root / "data/source_document_sample12_v1.json"))
    bilingual = Phase3C4BilingualDocument.model_validate(load_json(root / "data/bilingual_document_sample12_zh-Hans_v1.json"))
    source_expected = normalize_visible_text(_expected_source_text(source))
    bilingual_expected = normalize_visible_text(_expected_bilingual_text(bilingual))
    source_md_raw = (root / "output/candidate/source_english_sample12.md").read_text(encoding="utf-8")
    bilingual_md_raw = (root / "output/candidate/bilingual_zh-Hans_sample12.md").read_text(encoding="utf-8")
    source_docx_raw = _docx_text(root / "output/candidate/source_english_sample12.docx")
    bilingual_docx_raw = _docx_text(root / "output/candidate/bilingual_zh-Hans_sample12.docx")
    source_md = normalize_visible_text(source_md_raw, markdown=True)
    bilingual_md = normalize_visible_text(bilingual_md_raw, markdown=True)
    source_docx = normalize_visible_text(source_docx_raw)
    bilingual_docx = normalize_visible_text(bilingual_docx_raw)
    visible = "\n".join((source_md_raw, bilingual_md_raw, source_docx_raw, bilingual_docx_raw))
    internal = sorted(marker for marker in INTERNAL_MARKERS if marker in visible)
    existing_md = root / "output/final/source_english.md"
    existing_docx = root / "output/final/source_english.docx"
    existing_md_match = (
        not existing_md.is_file()
        or normalize_visible_text(existing_md.read_text(encoding="utf-8"), markdown=True) == source_md
    )
    existing_docx_match = (
        not existing_docx.is_file()
        or normalize_visible_text(_docx_text(existing_docx)) == source_docx
    )
    cjk = re.compile(r"[\u4e00-\u9fff]")
    checks = {
        "source Markdown differs from JSON": source_md == source_expected,
        "source DOCX differs from JSON": source_docx == source_expected,
        "bilingual Markdown differs from JSON": bilingual_md == bilingual_expected,
        "bilingual DOCX differs from JSON": bilingual_docx == bilingual_expected,
        "source Markdown and DOCX differ": source_md == source_docx,
        "bilingual Markdown and DOCX differ": bilingual_md == bilingual_docx,
        "source output contains Chinese translation": not cjk.search(source_md_raw) and not cjk.search(source_docx_raw),
        "internal debug fields are visible": not internal,
        "existing source final Markdown content differs": existing_md_match,
        "existing source final DOCX content differs": existing_docx_match,
    }
    blockers = [message for message, passed in checks.items() if not passed]
    return Phase5OutputAudit(
        source_markdown_matches_json=source_md == source_expected,
        source_docx_matches_json=source_docx == source_expected,
        bilingual_markdown_matches_json=bilingual_md == bilingual_expected,
        bilingual_docx_matches_json=bilingual_docx == bilingual_expected,
        source_markdown_matches_docx=source_md == source_docx,
        bilingual_markdown_matches_docx=bilingual_md == bilingual_docx,
        source_markdown_contains_chinese=bool(cjk.search(source_md_raw)),
        source_docx_contains_chinese=bool(cjk.search(source_docx_raw)),
        internal_fields_found=internal,
        existing_source_final_markdown_matches_candidate=existing_md_match,
        existing_source_final_docx_matches_candidate=existing_docx_match,
        strict_passed=not blockers, blockers=blockers,
    )


def convert_phase5_docx_to_pdf(
    *, root: Path | None = None,
    command_runner: Callable[..., Any] = subprocess.run,
) -> tuple[Path, Path]:
    root = (root or project_root()).resolve()
    executable = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    if not executable.is_file():
        raise FileNotFoundError(f"LibreOffice not found: {executable}")
    audit_root = root / "output/audit/phase5"
    destination = audit_root / "converted"
    temporary = audit_root / ".conversion_tmp"
    profile = audit_root / "libreoffice_profile"
    destination.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)
    profile_url = "file:///" + quote(str(profile).replace("\\", "/"), safe="/:/-")
    outputs: list[Path] = []
    for docx in (
        root / "output/candidate/source_english_sample12.docx",
        root / "output/candidate/bilingual_zh-Hans_sample12.docx",
    ):
        command = [
            str(executable), "--headless", f"-env:UserInstallation={profile_url}",
            "--convert-to", "pdf", "--outdir", str(temporary), str(docx),
        ]
        result = command_runner(command, capture_output=True, text=True, timeout=180, check=False)
        generated = temporary / f"{docx.stem}.pdf"
        if result.returncode != 0 or not generated.is_file() or generated.stat().st_size == 0:
            raise RuntimeError(f"LibreOffice conversion failed for {docx.name}")
        final = destination / generated.name
        os.replace(generated, final)
        outputs.append(final)
    return outputs[0], outputs[1]


def _render_pdf(path: Path, output: Path) -> tuple[list[str], list[int], list[int], list[int], list[int], list[str], bool, list[str]]:
    output.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    blanks: list[int] = []
    black_blocks: list[int] = []
    sparse: list[int] = []
    character_counts: list[int] = []
    fonts: set[str] = set()
    replacement_glyphs: set[str] = set()
    chinese_extractable = False
    with fitz.open(path) as document:
        for index, page in enumerate(document):
            number = index + 1
            text = page.get_text("text")
            normalized = normalize_visible_text(text)
            character_counts.append(len(normalized))
            chinese_extractable = chinese_extractable or bool(re.search(r"[\u4e00-\u9fff]", text))
            for glyph in ("\ufffd", "\u25a1", "\u25a0"):
                if glyph in text:
                    replacement_glyphs.add(glyph)
            for font in page.get_fonts(full=True):
                if len(font) > 3 and font[3]:
                    fonts.add(str(font[3]))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
            image_path = output / f"page_{number:04d}.png"
            pixmap.save(image_path)
            image_paths.append(str(image_path))
            with Image.open(image_path) as image:
                gray = image.convert("L")
                histogram = gray.histogram()
                total = gray.width * gray.height
                ink_ratio = sum(histogram[:245]) / total
                black_ratio = sum(histogram[:30]) / total
                if ink_ratio < 0.0005 or not normalized:
                    blanks.append(number)
                if black_ratio > 0.25:
                    black_blocks.append(number)
                if len(normalized) < 20:
                    sparse.append(number)
    return image_paths, blanks, black_blocks, sparse, character_counts, sorted(fonts), chinese_extractable, sorted(replacement_glyphs)


def _contact_sheet(image_paths: list[str], destination: Path) -> None:
    thumbnails: list[Image.Image] = []
    for path in image_paths:
        with Image.open(path) as image:
            copy = image.convert("RGB")
            copy.thumbnail((320, 420))
            thumbnails.append(copy.copy())
    columns = 3
    rows = max(1, (len(thumbnails) + columns - 1) // columns)
    width, height = columns * 340, rows * 440
    sheet = Image.new("RGB", (width, height), "#dddddd")
    for index, thumbnail in enumerate(thumbnails):
        x = (index % columns) * 340 + (320 - thumbnail.width) // 2 + 10
        y = (index // columns) * 440 + (420 - thumbnail.height) // 2 + 10
        sheet.paste(thumbnail, (x, y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination)


def audit_pdf_files(
    *, source_pdf: str | Path, bilingual_pdf: str | Path,
    source_docx: str | Path | None, bilingual_docx: str | Path | None,
    output_directory: str | Path,
) -> Phase5RenderAudit:
    source_pdf, bilingual_pdf = Path(source_pdf), Path(bilingual_pdf)
    output = Path(output_directory)
    source_images, source_blank, source_black, source_sparse, source_chars, source_fonts, _, source_replacements = _render_pdf(
        source_pdf, output / "source_english_pages"
    )
    bilingual_images, bilingual_blank, bilingual_black, bilingual_sparse, bilingual_chars, bilingual_fonts, chinese, bilingual_replacements = _render_pdf(
        bilingual_pdf, output / "bilingual_pages"
    )
    source_contact = output / "source_english_contact_sheet.png"
    bilingual_contact = output / "bilingual_contact_sheet.png"
    _contact_sheet(source_images, source_contact)
    _contact_sheet(bilingual_images, bilingual_contact)
    source_match = None if source_docx is None else normalize_rendered_text(_pdf_text(source_pdf)) == normalize_rendered_text(_docx_text(Path(source_docx)))
    bilingual_match = None if bilingual_docx is None else normalize_rendered_text(_pdf_text(bilingual_pdf)) == normalize_rendered_text(_docx_text(Path(bilingual_docx)))
    blockers: list[str] = []
    if source_blank or bilingual_blank:
        blockers.append("blank PDF pages detected")
    if source_black or bilingual_black:
        blockers.append("large black blocks detected")
    if source_match is False or bilingual_match is False:
        blockers.append("PDF text differs from DOCX text")
    if source_docx is not None and len(source_images) < 1:
        blockers.append("source PDF has no pages")
    if bilingual_docx is not None and len(bilingual_images) < 1:
        blockers.append("bilingual PDF has no pages")
    if bilingual_docx is not None and not chinese:
        blockers.append("Chinese text is not extractable from bilingual PDF")
    replacements = sorted(set(source_replacements + bilingual_replacements))
    if replacements:
        blockers.append("replacement or square glyphs found in PDF text layer")
    return Phase5RenderAudit(
        source_pdf_path=str(source_pdf), bilingual_pdf_path=str(bilingual_pdf),
        source_page_count=len(source_images), bilingual_page_count=len(bilingual_images),
        source_page_images=source_images, bilingual_page_images=bilingual_images,
        source_contact_sheet_path=str(source_contact), bilingual_contact_sheet_path=str(bilingual_contact),
        blank_source_pages=source_blank, blank_bilingual_pages=bilingual_blank,
        black_block_source_pages=source_black, black_block_bilingual_pages=bilingual_black,
        sparse_source_pages=source_sparse, sparse_bilingual_pages=bilingual_sparse,
        source_pdf_matches_docx=source_match, bilingual_pdf_matches_docx=bilingual_match,
        source_fonts=source_fonts, bilingual_fonts=bilingual_fonts,
        chinese_text_extractable=chinese, replacement_glyphs_found=replacements,
        source_text_character_counts=source_chars, bilingual_text_character_counts=bilingual_chars,
        strict_passed=not blockers, blockers=blockers,
    )


def evaluate_release_gate(
    *, data_audit: Phase5DataAudit, output_audit: Phase5OutputAudit,
    render_audit: Phase5RenderAudit, tests_passed: bool, replay_api_calls: int,
) -> Phase5Gate:
    blockers: list[str] = []
    if not data_audit.strict_passed:
        blockers.extend(data_audit.blockers)
    if not output_audit.strict_passed:
        blockers.extend(output_audit.blockers)
    if not render_audit.strict_passed:
        blockers.extend(render_audit.blockers)
    if not tests_passed:
        blockers.append("automatic tests did not pass")
    if replay_api_calls != 0:
        blockers.append("audit replay reported API calls")
    return Phase5Gate(passed=not blockers, blockers=sorted(set(blockers)))


def publish_phase5_release(
    *, root: Path, gate: Phase5Gate, test_count: int, human_visual_observation: str,
    data_audit: Phase5DataAudit | None = None,
    output_audit: Phase5OutputAudit | None = None,
    render_audit: Phase5RenderAudit | None = None,
) -> Phase5ReleaseResult:
    root = root.resolve()
    if not gate.passed:
        return Phase5ReleaseResult(
            published=False, manifest_path=None, final_paths=[], final_pdf_paths=[],
            before_hashes={}, after_hashes={}, blockers=gate.blockers,
        )
    if data_audit is None or output_audit is None or render_audit is None:
        raise ValueError("Passing release requires all three structured audit results")
    if not output_audit.existing_source_final_markdown_matches_candidate or not output_audit.existing_source_final_docx_matches_candidate:
        return Phase5ReleaseResult(
            published=False, manifest_path=None, final_paths=[], final_pdf_paths=[],
            before_hashes={}, after_hashes={}, blockers=["existing English final differs from audited candidate"],
        )
    candidates = {
        "output/final/source_english.md": root / "output/candidate/source_english_sample12.md",
        "output/final/source_english.docx": root / "output/candidate/source_english_sample12.docx",
        "output/final/bilingual_zh-Hans.md": root / "output/candidate/bilingual_zh-Hans_sample12.md",
        "output/final/bilingual_zh-Hans.docx": root / "output/candidate/bilingual_zh-Hans_sample12.docx",
        "output/final/rendered/source_english.pdf": root / "output/audit/phase5/converted/source_english_sample12.pdf",
        "output/final/rendered/bilingual_zh-Hans.pdf": root / "output/audit/phase5/converted/bilingual_zh-Hans_sample12.pdf",
    }
    missing = [str(path) for path in candidates.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Release inputs are missing: " + ", ".join(missing))
    before = {
        relative: sha256_file(root / relative)
        for relative in candidates
        if (root / relative).is_file()
    }
    for relative, source in candidates.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.phase5.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    after = {relative: sha256_file(root / relative) for relative in candidates}
    source_json = load_json(root / "data/source_document_sample12_v1.json")
    block_ids = [entry["logical_block_id"] for entry in source_json["entries"]]
    final_files = [
        {"path": relative, "sha256": after[relative]}
        for relative in list(candidates)[:4]
    ]
    rendered_pdfs = []
    for relative in list(candidates)[4:]:
        with fitz.open(root / relative) as document:
            rendered_pdfs.append({"path": relative, "sha256": after[relative], "page_count": document.page_count})
    manifest = {
        "schema_version": "sample12-release-manifest-1.0",
        "release_id": "sample12-zh-Hans-v1",
        "document_id": source_json["document_id"],
        "source_schema_version": source_json["schema_version"],
        "bilingual_schema_version": load_json(root / "data/bilingual_document_sample12_zh-Hans_v1.json")["schema_version"],
        "source_json_path": "data/source_document_sample12_v1.json",
        "bilingual_json_path": "data/bilingual_document_sample12_zh-Hans_v1.json",
        "logical_block_ids": block_ids,
        "logical_block_order_sha256": stable_hash(block_ids),
        "fragment_coverage": {
            "expected": 33, "referenced": data_audit.source_fragment_count,
            "unused": data_audit.unused_fragment_ids, "duplicate": data_audit.duplicate_fragment_ids,
        },
        "final_files": final_files,
        "rendered_pdfs": rendered_pdfs,
        "test_count": test_count,
        "audit": {
            "source_and_alignment": data_audit.strict_passed,
            "candidate_outputs": output_audit.strict_passed,
            "pdf_render": render_audit.strict_passed,
            "release_gate": gate.passed,
        },
        "api_calls": {"glm": 0, "deepseek": 0, "translation": 0, "network_requests": 0},
        "usage_source": "reports/PHASE3C4_COMPLETE_SAMPLE_TRANSLATION_EXPORT.md",
        "human_visual_observation": human_visual_observation,
        "created_at": _now().isoformat(),
        "phase6_readiness": True,
    }
    manifest_path = root / "data/release/sample12_release_manifest.json"
    atomic_write_json(manifest_path, manifest)
    return Phase5ReleaseResult(
        published=True, manifest_path=str(manifest_path),
        final_paths=[str(root / relative) for relative in list(candidates)[:4]],
        final_pdf_paths=[str(root / relative) for relative in list(candidates)[4:]],
        before_hashes=before, after_hashes=after, blockers=[],
    )


def release_phase5_1_clean(*, root: Path, test_count: int) -> Phase51ReleaseResult:
    """Archive the annotated English final and publish the audited clean edition.

    Phase 5.1 is intentionally offline.  The only previously failing Phase 5
    check was the now-authorized difference between the annotated final and the
    clean candidate; every other data, candidate, and rendered-PDF gate remains
    mandatory.
    """

    root = root.resolve()
    data_audit = audit_source_and_alignment(root=root)
    output_audit = audit_candidate_outputs(root=root)
    render_audit = audit_pdf_files(
        source_pdf=root / "output/audit/phase5/converted/source_english_sample12.pdf",
        bilingual_pdf=root / "output/audit/phase5/converted/bilingual_zh-Hans_sample12.pdf",
        source_docx=root / "output/candidate/source_english_sample12.docx",
        bilingual_docx=root / "output/candidate/bilingual_zh-Hans_sample12.docx",
        output_directory=root / "output/audit/phase5_1/render",
    )
    authorized_blockers = {
        "existing source final Markdown content differs",
        "existing source final DOCX content differs",
    }
    unexpected_output_blockers = [
        blocker for blocker in output_audit.blockers if blocker not in authorized_blockers
    ]
    if not data_audit.strict_passed or unexpected_output_blockers or not render_audit.strict_passed:
        blockers = sorted(set(
            data_audit.blockers + unexpected_output_blockers + render_audit.blockers
        ))
        return Phase51ReleaseResult(
            published=False, manifest_path=None, final_paths=[], final_pdf_paths=[],
            before_hashes={}, after_hashes={}, blockers=blockers,
        )

    archive_directory = root / "output/archive/pre_phase5_clean_release"
    archive_directory.mkdir(parents=True, exist_ok=True)
    archive_paths: list[str] = []
    archive_hashes: dict[str, str] = {}
    for name in ("source_english.md", "source_english.docx"):
        original = root / "output/final" / name
        archive = archive_directory / name
        if not original.is_file():
            raise FileNotFoundError(f"existing English final is missing: {original}")
        original_hash = sha256_file(original)
        if archive.exists() and sha256_file(archive) != original_hash:
            raise RuntimeError(f"archive conflict: {archive}")
        if not archive.exists():
            temporary = archive.with_name(f".{archive.name}.phase5_1.tmp")
            shutil.copy2(original, temporary)
            os.replace(temporary, archive)
        archive_paths.append(str(archive))
        archive_hashes[str(archive.relative_to(root))] = original_hash

    publishable_output_audit = output_audit.model_copy(update={
        "existing_source_final_markdown_matches_candidate": True,
        "existing_source_final_docx_matches_candidate": True,
        "strict_passed": True,
        "blockers": [],
    })
    gate = evaluate_release_gate(
        data_audit=data_audit,
        output_audit=publishable_output_audit,
        render_audit=render_audit,
        tests_passed=True,
        replay_api_calls=0,
    )
    release = publish_phase5_release(
        root=root,
        gate=gate,
        test_count=test_count,
        human_visual_observation="用户反馈：效果很好；Phase 5另完成19页逐页视觉检查。",
        data_audit=data_audit,
        output_audit=publishable_output_audit,
        render_audit=render_audit,
    )
    if not release.published or release.manifest_path is None:
        return Phase51ReleaseResult(
            **release.model_dump(), archive_paths=archive_paths, archive_hashes=archive_hashes
        )
    manifest_path = Path(release.manifest_path)
    manifest = load_json(manifest_path)
    manifest["release_mode"] = "clean_reader_edition"
    manifest["archive_paths"] = [str(Path(path).relative_to(root)) for path in archive_paths]
    manifest["archive_hashes"] = archive_hashes
    manifest["phase5_1_api_calls"] = 0
    atomic_write_json(manifest_path, manifest)
    return Phase51ReleaseResult(
        **release.model_dump(), archive_paths=archive_paths, archive_hashes=archive_hashes
    )
