"""Language-neutral note sections, reference anchors, and note-entry splitting."""

from __future__ import annotations

import re
from typing import Any


NOTE_HEADING_PATTERNS = (
    r"notes?", r"endnotes?", r"notes?\s+to\s+(?:chapter|part|section).+",
    r"anmerkungen", r"endnoten", r"notas", r"notas\s+del\s+cap[ií]tulo.+",
    r"notes?\s+du\s+chapitre.+", r"注", r"注釈", r"註", r"尾注", r"章末注",
)
NOTE_HEADING_RE = re.compile(r"^(?:" + "|".join(NOTE_HEADING_PATTERNS) + r")\s*$", re.I)
CHAPTER_NOTE_RE = re.compile(r"chapter|part|section|chapitre|kapitel|cap[ií]tulo|章", re.I)
MAJOR_HEADING_RE = re.compile(
    r"^(?:chapter|part|section|appendix|bibliography|references|index|"
    r"chapitre|kapitel|cap[ií]tulo|annexe|bibliographie|参考文献|索引|第.+章)\b", re.I,
)
NOTE_START_RE = re.compile(r"(?m)^\s*([0-9iIlLoO]{1,4})[.)]\s+")
NOTE_REFERENCE_RE = re.compile(r"(?<!\d)(?P<prefix>[.!?][\"'’”)]?)(?P<label>\d{1,4})(?=(?:\s|$))")
SYMBOL_NOTE_REFERENCE_RE = re.compile(r"(?P<prefix>(?<=\w)\s*)(?P<label>[*\u2020\u2021])(?=(?:\s|[),.;]|$))")
NOTE_PLACEHOLDER_RE = re.compile(r"\{\{NOTE_REF:([^:}]+):([^}]+)\}\}")


def note_scope_for_heading(text: str) -> str | None:
    value = " ".join(text.split()).strip()
    if not NOTE_HEADING_RE.fullmatch(value):
        return None
    return "chapter_endnote" if CHAPTER_NOTE_RE.search(value) else "document_endnote"


def classify_note_blocks(page_blocks: dict[int, list[dict[str, Any]]],
                         classifications: dict[int, dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    """Classify terminal or chapter-scoped note blocks without book-specific data."""
    result: dict[tuple[int, int], dict[str, Any]] = {}
    active_scope: str | None = None
    section_index = 0
    for page_no in sorted(page_blocks):
        for block_index, block in enumerate(page_blocks[page_no], 1):
            text = str(block.get("text") or "").strip()
            scope = note_scope_for_heading(text)
            if scope:
                active_scope = scope; section_index += 1
                result[(page_no, block_index)] = {
                    "element_type": "note_heading", "note_scope": scope,
                    "note_section_id": f"note-section-{section_index:04d}",
                }
                continue
            if (active_scope == "chapter_endnote" and classifications.get(page_no, {}).get("chapter_boundary")
                    and MAJOR_HEADING_RE.match(text)):
                active_scope = None
            if active_scope:
                result[(page_no, block_index)] = {
                    "element_type": active_scope, "note_scope": active_scope,
                    "note_section_id": f"note-section-{section_index:04d}",
                }
    return result


def split_note_entries(text: str) -> list[tuple[str | None, str]]:
    """Split numbered entries while retaining unnumbered/continuation prose."""
    matches = list(NOTE_START_RE.finditer(text))
    if not matches:
        return [(None, text.strip())] if text.strip() else []
    result: list[tuple[str | None, str]] = []
    prefix = text[:matches[0].start()].strip()
    if prefix:
        result.append((None, prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip()
        if value:
            result.append((match.group(1), value))
    return result


def normalize_note_label(value: str | None, previous_numeric: int | None = None) -> str | None:
    """Resolve OCR-confusable numbering only when sequence context supports it."""
    if value is None or value.isdigit():
        return value
    lowered = value.lower()
    digit_candidate = lowered.translate(str.maketrans({"i": "1", "l": "1", "o": "0"}))
    digit_value = int(digit_candidate) if digit_candidate.isdigit() else None
    roman_value = None
    if set(lowered) <= {"i", "v", "x", "l", "c", "d", "m"}:
        totals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
        roman_value = 0
        for index, character in enumerate(lowered):
            amount = totals[character]
            roman_value += -amount if index + 1 < len(lowered) and amount < totals[lowered[index + 1]] else amount
    if previous_numeric is not None:
        expected = previous_numeric + 1
        if digit_value == expected:
            return str(digit_value)
        if roman_value == expected:
            return str(roman_value)
    if any(character.isdigit() or character in {"o", "l"} for character in lowered) and digit_value is not None:
        return str(digit_value)
    return str(roman_value) if roman_value is not None else value


def note_id(scope: str, section_id: str, label: str) -> str:
    safe_label = re.sub(r"[^0-9A-Za-z_-]+", "-", label).strip("-") or f"u{ord(label[0]):04x}"
    return f"{section_id}-{scope}-{safe_label}"


def annotate_note_references(text: str, known_notes: dict[str, str], *, page_no: int,
                             occurrence_counts: dict[str, int]) -> tuple[str, list[dict[str, str]]]:
    links: list[dict[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        label = match.group("label")
        target = known_notes.get(label)
        if not target:
            return match.group(0)
        occurrence_counts[label] = occurrence_counts.get(label, 0) + 1
        safe_label = re.sub(r"[^0-9A-Za-z_-]+", "-", label).strip("-") or f"u{ord(label[0]):04x}"
        reference_id = f"note-ref-p{page_no:04d}-{safe_label}-{occurrence_counts[label]:03d}"
        placeholder = f"{{{{NOTE_REF:{label}:{reference_id}}}}}"
        links.append({"label": label, "note_id": target, "reference_id": reference_id,
                      "placeholder": placeholder})
        return match.group("prefix") + placeholder

    return SYMBOL_NOTE_REFERENCE_RE.sub(replace, NOTE_REFERENCE_RE.sub(replace, text)), links


def find_note_reference_labels(text: str) -> list[str]:
    """Return conservative reference candidates for unresolved-link reporting."""
    matches = list(NOTE_REFERENCE_RE.finditer(text)) + list(SYMBOL_NOTE_REFERENCE_RE.finditer(text))
    return [match.group("label") for match in sorted(matches, key=lambda item: item.start())]
