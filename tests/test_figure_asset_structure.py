"""Tests for Phase 4: Figures, Maps, Captions, and Assets."""

from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

import pytest

from bookflow.figure_assets import process_figures_and_maps, verify_gate


@pytest.fixture
def root() -> Path:
    return Path(".")


class TestFigureAssets:
    """Tests that process_figures_and_maps produces correct outputs."""

    def test_process_returns_path_and_stats(self, root: Path):
        path, stats = process_figures_and_maps(root)
        assert path.is_file()
        # Figures now include per-caption records, so count > 34
        assert stats["figure_count"] >= 34
        assert stats["map_count"] == 2

    def test_figure_manifest_exists(self, root: Path):
        path = root / "data/fullbook/assets/figures/figure_manifest.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) >= 34

    def test_map_manifest_exists(self, root: Path):
        path = root / "data/fullbook/assets/maps/map_manifest.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) == 2

    def test_caption_links_exist(self, root: Path):
        path = root / "data/fullbook/assets/captions/caption_links.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) > 0

    def test_asset_manifest_exists(self, root: Path):
        path = root / "data/fullbook/assets/asset_manifest.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        # Assets are now per-page, not per-figure
        assert len(records) >= 34  # At least 34 unique pages with figures

    def test_no_plate_verso_as_figure(self, root: Path):
        path = root / "data/fullbook/assets/figures/figure_manifest.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        page_map_path = root / "data/fullbook/structure/final/page_map.jsonl"
        pm_records = [json.loads(l) for l in page_map_path.read_text("utf-8").splitlines() if l.strip()]
        plate_verso = {
            r["physical_page"] for r in pm_records
            if r.get("blank_detail") and r["blank_detail"].get("blank_kind") == "plate_verso_blank"
        }
        for rec in records:
            assert rec["source_page"] not in plate_verso, (
                f"Figure {rec['figure_id']} on p{rec['source_page']} is a plate_verso_blank"
            )

    def test_all_asset_refs_relative(self, root: Path):
        asset_path = root / "data/fullbook/assets/asset_manifest.jsonl"
        records = [json.loads(l) for l in asset_path.read_text("utf-8").splitlines() if l.strip()]
        for rec in records:
            ref = rec.get("source_page_asset_ref", "")
            assert ref.startswith("data/"), f"Non-relative path: {ref}"

    def test_asset_files_exist_and_hash_match(self, root: Path):
        from bookflow.io_utils import sha256_file
        asset_path = root / "data/fullbook/assets/asset_manifest.jsonl"
        records = [json.loads(l) for l in asset_path.read_text("utf-8").splitlines() if l.strip()]
        for rec in records[:5]:
            ref = rec["figure_asset_ref"]
            path = root / ref
            assert path.is_file(), f"Asset file not found: {ref}"
            actual = sha256_file(path)
            assert actual == rec["figure_asset_sha256"], f"Hash mismatch for {rec['asset_id']}"

    def test_digitization_artifacts_filtered(self, root: Path):
        cap_path = root / "data/fullbook/assets/captions/caption_links.jsonl"
        records = [json.loads(l) for l in cap_path.read_text("utf-8").splitlines() if l.strip()]
        for rec in records:
            text = rec.get("caption_text", "")
            assert "Digitized by Microsoft" not in text, f"Digitization artifact not filtered: {text}"
            assert "Univ Calif" not in text, f"Digitization artifact not filtered: {text}"

    def test_frontispiece_has_captions(self, root: Path):
        fig_path = root / "data/fullbook/assets/figures/figure_manifest.jsonl"
        records = [json.loads(l) for l in fig_path.read_text("utf-8").splitlines() if l.strip()]
        frontispiece = [r for r in records if r["figure_type"] == "frontispiece"]
        assert len(frontispiece) >= 1
        assert any("TAKIN" in " ".join(r["caption_texts"]).upper() for r in frontispiece)

    def test_caption_count_does_not_determine_figure_count(self, root: Path):
        """Caption candidates remain separate from evidence-backed regions."""
        fig_path = root / "data/fullbook/assets/figures/figure_manifest.jsonl"
        map_path = root / "data/fullbook/assets/maps/map_manifest.jsonl"
        cap_path = root / "data/fullbook/assets/captions/caption_links.jsonl"
        figures = [json.loads(l) for l in fig_path.read_text("utf-8").splitlines() if l.strip()]
        maps = [json.loads(l) for l in map_path.read_text("utf-8").splitlines() if l.strip()]
        captions = [json.loads(l) for l in cap_path.read_text("utf-8").splitlines() if l.strip()]

        cap_counts = Counter(c["source_page"] for c in captions)
        all_figs = figures + maps
        fig_page_counts = Counter(f["source_page"] for f in all_figs)

        assert cap_counts[6] == 2 and fig_page_counts[6] == 1
        assert cap_counts[43] == 2 and fig_page_counts[43] == 2

    def test_p43_confirmed_captions_are_one_to_one(self, root: Path):
        records = [json.loads(l) for l in (root / "data/fullbook/assets/figures/figure_manifest.jsonl").read_text("utf-8").splitlines() if l.strip()]
        p43 = [r for r in records if r["source_page"] == 43 and r["region_status"] == "confirmed"]
        assert [r["caption_texts"] for r in p43] == [["A VIEW ON THE YANGTSE-KIANG."], ["TEMPLES ON HWA-SHAN."]]
        ids = [caption_id for record in p43 for caption_id in record["caption_ids"]]
        assert len(ids) == len(set(ids)) == 2

    def test_every_figure_has_region_evidence(self, root: Path):
        """Every figure must have region_marker and region_inseparable_evidence."""
        fig_path = root / "data/fullbook/assets/figures/figure_manifest.jsonl"
        records = [json.loads(l) for l in fig_path.read_text("utf-8").splitlines() if l.strip()]
        for rec in records:
            assert rec.get("region_marker"), f"Figure {rec['figure_id']} missing region_marker"
            assert rec.get("region_bbox") or rec.get("region_inseparable_evidence"), f"Figure {rec['figure_id']} missing region evidence"

    def test_asset_manifest_is_per_page_not_per_figure(self, root: Path):
        """Asset manifest should have one entry per unique source page, not per figure."""
        asset_path = root / "data/fullbook/assets/asset_manifest.jsonl"
        fig_path = root / "data/fullbook/assets/figures/figure_manifest.jsonl"
        map_path = root / "data/fullbook/assets/maps/map_manifest.jsonl"
        assets = [json.loads(l) for l in asset_path.read_text("utf-8").splitlines() if l.strip()]
        figures = [json.loads(l) for l in fig_path.read_text("utf-8").splitlines() if l.strip()]
        maps = [json.loads(l) for l in map_path.read_text("utf-8").splitlines() if l.strip()]

        asset_pages = {a["source_page"] for a in assets}
        fig_pages = {f["source_page"] for f in figures + maps}
        assert asset_pages == fig_pages, "Asset pages should match figure pages"
        assert len(assets) == len(asset_pages), "Should be one asset per unique page"


class TestFigureAssetsGate:
    """Tests for the Phase 4 gate verification."""

    def test_gate_passes(self, root: Path):
        passed, messages = verify_gate(root)
        assert passed, f"Gate failed: {messages}"

    def test_gate_checkpoint(self, root: Path):
        path = root / "data/fullbook/checkpoints/phase_4_checkpoint.json"
        assert path.is_file()
        cp = json.loads(path.read_text("utf-8"))
        assert cp["phase"] == "phase_4"
        assert cp["status"] == "completed"
        assert cp["figure_count"] >= 34
        assert cp["map_count"] == 2
        assert cp["api_calls"] == 0
        assert cp["page_asset_count"] >= 34
        assert cp["multi_figure_pages"] > 0
