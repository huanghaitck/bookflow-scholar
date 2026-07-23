"""Final integrity test for Phase 2-6: verifies source hash, 412 pages,
30 chapters, 971 units, 33 plate-verso blanks remain blank, back-matter
ranges, frozen hashes, asset existence/hashes, relative paths, no secrets,
and no Phase 7 output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bookflow.io_utils import sha256_file


@pytest.fixture
def root() -> Path:
    return Path(".")


FROZEN_FILES = {
    "page_map_phase1b": ("data/fullbook/structure/registry/page_map.jsonl",
                         "11115a628afb806267fd15f48731550705a938cd3f3d2fc87db82f295db2f5ad"),
    "bridge_candidates": ("data/fullbook/structure/bridges/bridge_candidates.jsonl",
                          "169a214c10171a5fecaa758d066d541200c05d7c81be3dd7c059c58607ce814a"),
    "boundaries": ("data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
                   "b08c4bab8506f6d85cfd5e48b54ec801bd1868e10e6fb1375779011c08faf5a1"),
    "source_document": ("data/fullbook/main_text/source_document_main_text_v1.json",
                        "f18ad3eefa24eec1241dbe69a8baa0ecd8512a998a4796b5636c8f980cc01c8a"),
    "bilingual_document": ("data/fullbook/main_text/bilingual_document_main_text_zh-Hans_v1.json",
                           "d00f42fecbd0f8410019a2b9cfb42eeba0a2dc6f97188fdcb00cce8acf4bdc8b"),
}


class TestFrozenIntegrity:
    """All frozen files must remain unchanged."""

    @pytest.mark.parametrize("name,rel,expected", [
        (n, r, e) for n, (r, e) in FROZEN_FILES.items()
    ])
    def test_frozen_file_unchanged(self, root, name, rel, expected):
        actual = sha256_file(root / rel)
        assert actual == expected, f"Frozen file {name} hash changed: {actual} != {expected}"


class TestCanonicalCounts:
    """Canonical document has correct counts."""

    def test_412_pages(self, root):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert len(data["physical_page_order"]) == 412

    def test_30_chapters(self, root):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        chapters = [s for s in data["sections"] if s["section_type"] == "chapter"]
        assert len(chapters) == 30

    def test_971_units(self, root):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert len(data["logical_units"]) == 971

    def test_33_plate_verso_blanks_remain_blank(self, root):
        path = root / "data/fullbook/structure/final/page_map.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        plate_verso = [
            r for r in records
            if r.get("blank_detail") and r["blank_detail"].get("blank_kind") == "plate_verso_blank"
        ]
        assert len(plate_verso) == 33

    def test_back_matter_ranges(self, root):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        app_a = next(a for a in data["appendices"] if a["appendix_id"] == "appendix_a")
        assert app_a["physical_page_start"] == 381
        assert app_a["physical_page_end"] == 397
        app_c = next(a for a in data["appendices"] if a["appendix_id"] == "appendix_c")
        assert app_c["physical_page_end"] == 404

    def test_index_pages_405_408(self, root):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        idx_pages = sorted(set(e["physical_page"] for e in data["index_entries"] + data["index_entry_groups"]))
        assert idx_pages == [405, 406, 407, 408]


class TestAssetIntegrity:
    """All assets exist and hash-match."""

    def test_all_asset_files_exist(self, root):
        path = root / "data/fullbook/assets/asset_manifest.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for rec in records:
            ref = rec["figure_asset_ref"]
            assert (root / ref).is_file(), f"Asset file not found: {ref}"

    def test_all_asset_hashes_match(self, root):
        path = root / "data/fullbook/assets/asset_manifest.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for rec in records:
            ref = rec["figure_asset_ref"]
            actual = sha256_file(root / ref)
            assert actual == rec["figure_asset_sha256"], f"Hash mismatch for {rec['asset_id']}"

    def test_all_paths_relative(self, root):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        content = path.read_text("utf-8")
        assert "D:\\" not in content
        assert "C:\\" not in content


class TestNoSecrets:
    """No secrets in any output."""

    @pytest.mark.parametrize("rel", [
        "data/fullbook/canonical/canonical_book_document_v1.json",
        "data/fullbook/canonical/canonical_book_manifest_v1.json",
        "data/fullbook/canonical/canonical_validation_report_v1.json",
    ])
    def test_no_secrets_in_output(self, root, rel):
        content = (root / rel).read_text("utf-8").lower()
        assert "api_key" not in content
        assert "authorization" not in content
        assert "bearer" not in content
        assert "data:image" not in content
        assert "base64," not in content


class TestPhaseBoundary:
    """Later authorized phases must not mutate the frozen Phase 6 baseline."""

    def test_phase6_frozen_during_phase13_6(self, root):
        import hashlib
        canonical = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        assert hashlib.sha256(canonical.read_bytes()).hexdigest() == "16c1c9ba4d60d1c2a4124433291a1a56bf499384215c720f6988e6e183c01326"
        state_path = root / "docs/CURRENT_PROJECT_STATE.yaml"
        state = yaml.safe_load(state_path.read_text("utf-8"))
        assert state["phase_2_to_6"]["phases_completed"]["phase_6"]["status"] == "completed"
        assert state["phase_2_to_6"]["phases_completed"]["phase_6"]["canonical_sha256"] == (
            "16c1c9ba4d60d1c2a4124433291a1a56bf499384215c720f6988e6e183c01326"
        )
        assert state["current_phase"] == "phase_13_6"
        assert state["phase_13_6"]["phase_14b_entered"] is False
