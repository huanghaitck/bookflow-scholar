from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .io_utils import atomic_write_jsonl

RAW_TOC_OVERRIDES = {
    26: "ON THE FRINGE OF THE DESERT, AND SOME ACCOUNT ON PRZEWALSKI'S GAZELLE (Gazella przewalskii)",
}


def normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def build_title_provenance(root: Path, canonical: dict) -> list[dict]:
    source = json.loads((root / "data/fullbook/main_text/source_document_main_text_v1.json").read_text("utf-8"))
    by_page = {}
    for entry in source["entries"]:
        if entry.get("block_type") == "section_title":
            for page in entry.get("source_pages", []):
                by_page.setdefault(page, []).append(entry["source_text"])
    records = []
    for chapter in [s for s in canonical["sections"] if s["section_type"] == "chapter"]:
        number = chapter["chapter_number"]
        raw_open = " ".join(by_page.get(chapter["start_page"], [])) or chapter["display_title"]
        raw_toc = RAW_TOC_OVERRIDES.get(number, chapter.get("toc_candidate", chapter["canonical_title"]))
        records.append({
            "chapter_id": chapter["section_id"], "chapter_number": number,
            "raw_chapter_open_candidate": raw_open,
            "normalized_chapter_open_candidate": normalize_title(raw_open),
            "raw_toc_candidate": raw_toc,
            "normalized_toc_candidate": normalize_title(raw_toc),
            "canonical_title": chapter["canonical_title"], "display_title": chapter["display_title"],
            "title_variants": chapter.get("title_variants", []), "title_conflict": chapter.get("title_conflict"),
            "title_resolution_source": chapter["title_source"], "title_evidence": chapter["title_evidence"],
            "raw_candidate_sha256": hashlib.sha256((raw_open + "\n" + raw_toc).encode()).hexdigest(),
        })
    out = root / "data/fullbook/multilingual/provenance/source_title_candidates_v1.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_jsonl(out, records)
    return records
