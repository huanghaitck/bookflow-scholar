from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .io_utils import atomic_write_json, load_json


DEFERRED_REASON = "complex_back_matter_deferred_for_later_release"
TERMINAL_PAGE_STATUSES = {"completed", "cache_reused", "final_blank", "blank", "legal_nontext"}


def effective_translatable_block_type(
    block_type: str, original_label: str | None, text: str, page_type: str | None = None
) -> str | None:
    if not text.strip():
        return None
    if block_type in {"header", "footer", "page_number"}:
        return None
    if original_label in {"watermark", "decorative_text", "decoration"}:
        return None
    illustration_pages = {
        "illustration", "illustrated", "illustrated_page", "illustration_page", "image", "map"
    }
    if block_type == "illustration" or (
        page_type in illustration_pages and block_type in {"body", "unknown", "caption"}
    ):
        return "caption"
    if block_type == "unknown":
        return "other_translatable"
    return block_type


def effective_body_page_pairs(pages: list[object]) -> list[tuple[int, int]]:
    body_pages = [
        int(getattr(page, "pdf_page"))
        for page in pages
        if any(
            getattr(fragment, "block_type", None) == "body"
            for fragment in getattr(page, "content_fragments", [])
        )
    ]
    return list(zip(body_pages, body_pages[1:]))


def validated_pair_resolution(
    *,
    model_status: str,
    structural_break: str,
    join_operation: str,
    hyphen_type: str,
    word_continuation: bool | None,
    sentence_continuation: bool | None,
    paragraph_continuation: bool | None,
    visible_trailing_hyphen: bool,
    left_token: str = "",
    right_token: str = "",
) -> dict[str, object] | None:
    del right_token  # reserved for stricter lexical validation without changing fingerprints
    known_breaks = {"paragraph_break", "section_break", "chapter_break", "illustration_break"}
    if structural_break in known_breaks and join_operation == "no_join":
        return {
            "auto_resolution_status": "resolved_pair",
            "structural_break": structural_break,
            "join_operation": "no_join",
            "word_continuation": False,
            "sentence_continuation": False,
            "paragraph_continuation": False,
        }
    if (
        model_status == "completed"
        and structural_break == "none"
        and sentence_continuation is True
        and paragraph_continuation is True
    ):
        if not visible_trailing_hyphen and join_operation in {
            "concatenate_with_space", "concatenate_without_space"
        }:
            return {
                "auto_resolution_status": "resolved_pair",
                "structural_break": "none",
                "join_operation": "insert_space",
                "word_continuation": False,
                "sentence_continuation": True,
                "paragraph_continuation": True,
            }
        if (
            visible_trailing_hyphen
            and word_continuation is True
            and join_operation == "concatenate_without_space"
        ):
            return {
                "auto_resolution_status": "resolved_pair",
                "structural_break": "none",
                "join_operation": "remove_layout_hyphen",
                "word_continuation": True,
                "sentence_continuation": True,
                "paragraph_continuation": True,
            }
        if visible_trailing_hyphen and hyphen_type in {"line_break_hyphen", "lexical_hyphen"}:
            return {
                "auto_resolution_status": "resolved_pair",
                "structural_break": "none",
                "join_operation": (
                    "remove_layout_hyphen"
                    if hyphen_type == "line_break_hyphen"
                    else "preserve_lexical_hyphen"
                ),
                "word_continuation": True,
                "sentence_continuation": True,
                "paragraph_continuation": True,
            }
    function_words = {
        "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or",
        "the", "to", "with", "who", "whose", "which", "that",
    }
    if (
        model_status == "completed"
        and structural_break == "paragraph_break"
        and sentence_continuation is True
        and paragraph_continuation is False
        and not visible_trailing_hyphen
        and left_token.lower() in function_words
    ):
        return {
            "auto_resolution_status": "resolved_pair",
            "structural_break": "none",
            "join_operation": "insert_space",
            "word_continuation": False,
            "sentence_continuation": True,
            "paragraph_continuation": True,
        }
    return None


def boundary_leaves_fragment_unresolved(
    boundary: object | None, group: set[str], *, incoming: bool
) -> bool:
    if boundary is None:
        return True
    if getattr(boundary, "auto_resolution_status", None) == "unresolved":
        return True
    if getattr(boundary, "paragraph_continuation", None) is False:
        return False
    previous = getattr(boundary, "previous_fragment_id", None)
    following = getattr(boundary, "next_fragment_id", None)
    expected = following if incoming else previous
    return expected not in group or previous not in group or following not in group


class MainTextScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_scope: Literal["main_text_edition"] = "main_text_edition"
    source_pdf: str
    source_pdf_sha256: str
    source_page_start: Literal[1] = 1
    source_page_end: Literal[379] = 379
    full_pdf_actual_page_count: int = Field(ge=379)
    deferred_page_start: Literal[380] = 380
    deferred_page_end: int | None = None
    is_complete_full_book: Literal[False] = False
    is_complete_main_text_edition: Literal[True] = True

    @model_validator(mode="after")
    def set_deferred_end(self) -> "MainTextScope":
        expected = self.full_pdf_actual_page_count
        if self.deferred_page_end is None:
            self.deferred_page_end = expected
        elif self.deferred_page_end != expected:
            raise ValueError("deferred_page_end must equal the PDF actual page count")
        return self

    @property
    def actual_page_count(self) -> int:
        """Compatibility view for the existing full-book page adapter."""

        return self.source_page_end


class MainTextTerminalAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    terminal_pages: int
    blank_pages: list[int]
    missing_pages: list[int]
    unresolved_pages: list[int]
    in_flight_pages: list[int]
    out_of_scope_quarantine_pages: list[int]
    source_pdf_hash_matches: bool


def deferred_page_records(scope: MainTextScope) -> list[dict[str, object]]:
    return [
        {
            "pdf_page": page,
            "processing_status": "deferred",
            "deferred_reason": DEFERRED_REASON,
        }
        for page in range(scope.deferred_page_start, int(scope.deferred_page_end) + 1)
    ]


def write_scope_artifacts(root: Path, scope: MainTextScope) -> tuple[Path, Path]:
    directory = root.resolve() / "data" / "fullbook" / "main_text"
    scope_path = directory / "scope.json"
    deferred_path = directory / "deferred_pages.json"
    atomic_write_json(scope_path, scope)
    atomic_write_json(deferred_path, deferred_page_records(scope))
    return scope_path, deferred_path


def _page_from_key(value: str) -> int | None:
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) if digits else None


def verified_blank_state(root: Path, page: int) -> dict[str, object] | None:
    path = (
        root.resolve()
        / "data"
        / "fullbook"
        / "vision"
        / "final_states"
        / f"page_{page:04d}.json"
    )
    if not path.is_file():
        return None
    payload = load_json(path)
    if not (
        payload.get("pdf_page") == page
        and payload.get("human_verified") is True
        and payload.get("page_type") == "blank"
        and payload.get("quarantine") is False
        and payload.get("source_fragments") == []
    ):
        return None
    return payload


def audit_main_text_terminal_state(root: Path, scope: MainTextScope) -> MainTextTerminalAudit:
    root = root.resolve()
    vision = root / "data" / "fullbook" / "vision"
    missing: list[int] = []
    unresolved: list[int] = []
    blanks: list[int] = []
    for page in range(scope.source_page_start, scope.source_page_end + 1):
        cache_path = vision / "cache" / f"page_{page:04d}.json"
        if not cache_path.is_file():
            missing.append(page)
            continue
        cache = load_json(cache_path)
        status = str(cache.get("status", ""))
        if status not in TERMINAL_PAGE_STATUSES:
            unresolved.append(page)
            continue
        if status in {"final_blank", "blank"}:
            if verified_blank_state(root, page) is None:
                unresolved.append(page)
                continue
            blanks.append(page)

    checkpoint_path = root / "data" / "fullbook" / "checkpoints" / "production.json"
    checkpoint = load_json(checkpoint_path) if checkpoint_path.is_file() else {}
    quarantine = checkpoint.get("quarantine", {}).get("vision_single", {})
    out_of_scope_quarantine: list[int] = []
    for key in quarantine:
        page = _page_from_key(str(key))
        if page is None:
            continue
        if scope.source_page_start <= page <= scope.source_page_end:
            unresolved.append(page)
        else:
            out_of_scope_quarantine.append(page)

    ledger_path = vision / "call_ledger.json"
    ledger = load_json(ledger_path) if ledger_path.is_file() else {}
    in_flight = sorted({
        int(attempt.get("pdf_page"))
        for attempt in ledger.get("attempts", [])
        if attempt.get("status") in {"in_flight", "started", "pending"}
        and attempt.get("pdf_page") is not None
        and scope.source_page_start <= int(attempt.get("pdf_page")) <= scope.source_page_end
    })
    hash_matches = bool(
        checkpoint.get("source_pdf_sha256") == scope.source_pdf_sha256
        and ledger.get("source_pdf_sha256") == scope.source_pdf_sha256
    )
    unresolved = sorted(set(unresolved))
    terminal = scope.source_page_end - scope.source_page_start + 1 - len(missing) - len(unresolved)
    passed = not missing and not unresolved and not in_flight and hash_matches
    return MainTextTerminalAudit(
        passed=passed,
        terminal_pages=terminal,
        blank_pages=sorted(blanks),
        missing_pages=missing,
        unresolved_pages=unresolved,
        in_flight_pages=in_flight,
        out_of_scope_quarantine_pages=sorted(out_of_scope_quarantine),
        source_pdf_hash_matches=hash_matches,
    )
