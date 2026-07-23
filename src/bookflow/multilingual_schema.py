from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import re

TRANSLATION_STATUSES = {
    "reused_frozen", "pending", "translated", "validated", "preserve_source",
    "skip", "blocked_by_source_quality", "failed_retryable", "failed_terminal", "stale_source",
}

# Only explicit application placeholders are protected. Ordinary book text --
# numbers, units, years, Roman numerals, parentheses, Latin names, apostrophes,
# and hyphens -- remains visible to the translation model.
PROTECTED_PATTERN = re.compile(r"\{\{[A-Za-z0-9_.:-]+\}\}|\[\[[A-Za-z0-9_.:-]+\]\]")


def protect_placeholders(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"__BOOKFLOW_PH_{len(mapping):04d}__"
        mapping[key] = match.group(0)
        return key

    return PROTECTED_PATTERN.sub(replace, text), mapping


def restore_placeholders(text: str, mapping: dict[str, str]) -> str:
    if any(text.count(key) != 1 for key in mapping):
        raise ValueError("placeholder mismatch")
    for key, value in mapping.items():
        text = text.replace(key, value)
    if "__BOOKFLOW_PH_" in text:
        raise ValueError("unknown placeholder")
    return text


@dataclass(frozen=True)
class TranslationUnit:
    translation_unit_id: str
    source_object_id: str
    source_object_type: str
    source_language: str
    target_language: str
    source_text: str
    source_text_sha256: str
    context_before: str
    context_after: str
    section_id: str | None
    physical_pages: list[int]
    printed_pages: list[int]
    translation_policy: str
    translation_status: str
    existing_translation_ref: str | None
    cache_key: str
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        assert self.translation_status in TRANSLATION_STATUSES
        return asdict(self)
