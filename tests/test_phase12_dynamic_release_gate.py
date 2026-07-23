from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookflow.io_utils import atomic_write_json, atomic_write_jsonl
from bookflow.production import calculate_release_eligibility, calculate_live_build_statistics, enforce_release_gate


def canonical_fixture():
    return {
        "figures": [{"region_status": "pending_review"}, {"region_status": "confirmed"}],
        "maps": [{"region_status": "pending_review"}],
        "table_row_groups": [{}, {}, {}, {}],
        "table_cells": [{"cell_parse_status": "candidate"}, {"cell_parse_status": "confirmed"}, {"cell_parse_status": "candidate"}],
        "index_entry_groups": [{"parse_status": "pending_review"} for _ in range(5)],
    }


def workspace(tmp_path, statuses, *, cache=True, overlay=True, manifest=True, checkpoint="completed"):
    base = tmp_path / "data/fullbook/multilingual"
    states = []
    translations = {}
    for index, status in enumerate(statuses):
        uid = f"u{index}"; source_sha = f"sha{index}"; fp = f"{index:064x}"
        state = {"translation_unit_id": uid, "source_text_sha256": source_sha, "status": status}
        if status in {"validated", "translated"}:
            state["cache_fingerprint"] = fp
            if cache:
                atomic_write_json(base / "cache" / fp[:2] / f"{fp}.json", {"translation_unit_id": uid, "source_object_id": f"o{index}", "source_text_sha256": source_sha, "translated_text": "译文", "validated": True})
            if overlay: translations[uid] = {"translated_text": "译文"}
        states.append(state)
    atomic_write_jsonl(base / "state/translation_state_zh-Hans_v1.jsonl", states)
    if overlay: atomic_write_json(base / "documents/multilingual_translation_overlay_zh-Hans_v1.json", {"translations": translations})
    counts = {}
    for state in states: counts[state["status"]] = counts.get(state["status"], 0) + 1
    atomic_write_json(base / "multilingual_book_manifest_v1.json", {"status_counts": counts if manifest else {"pending": 999}})
    atomic_write_json(base / "checkpoints/translation_zh-Hans_production.json", {"status": checkpoint})
    return calculate_release_eligibility(tmp_path, canonical_fixture())


def test_pending_zero_allows_zh_hans_release(tmp_path):
    result = workspace(tmp_path, ["validated", "preserve_source", "blocked_by_source_quality"])
    enforce_release_gate(result, "zh-Hans", "release"); assert result["eligible"]


def test_pending_zero_allows_bilingual_release(tmp_path):
    result = workspace(tmp_path, ["validated"])
    enforce_release_gate(result, "bilingual", "release"); assert result["eligible"]


def test_pending_one_reports_live_one_not_legacy_count(tmp_path):
    result = workspace(tmp_path, ["pending"])
    with pytest.raises(RuntimeError, match="pending=1") as exc: enforce_release_gate(result, "zh-Hans", "release")
    assert "334" not in str(exc.value)


def test_preserve_source_does_not_block(tmp_path):
    result = workspace(tmp_path, ["preserve_source", "preserve_source"])
    assert result["eligible"] and result["pending_translatable"] == 0


def test_blocked_by_source_quality_is_not_pending(tmp_path):
    result = workspace(tmp_path, ["blocked_by_source_quality"])
    assert result["eligible"] and result["pending_translatable"] == 0


@pytest.mark.parametrize("missing", ["cache", "overlay"])
def test_validated_cache_or_overlay_missing_rejects(tmp_path, missing):
    result = workspace(tmp_path, ["validated"], cache=missing != "cache", overlay=missing != "overlay")
    assert not result["eligible"] and any(missing in blocker for blocker in result["blockers"])


def test_manifest_mismatch_and_checkpoint_not_completed_reject(tmp_path):
    result = workspace(tmp_path, ["validated"], manifest=False, checkpoint="resumable")
    assert "manifest_state_mismatch" in result["blockers"] and "production_checkpoint_not_completed" in result["blockers"]


def test_fixture_counts_prove_build_statistics_are_dynamic(tmp_path):
    result = workspace(tmp_path, ["pending", "preserve_source", "preserve_source", "blocked_by_source_quality", "blocked_by_source_quality", "blocked_by_source_quality"])
    assert result["fallback_counts"] == {"pending_source_fallback": 1, "preserve_source": 2, "blocked_source_quality_fallback": 3}
    assert result["degraded_structure"] == {"pending_figure_regions": 2, "table_row_groups": 4, "candidate_cells": 2, "pending_index_groups": 5}
