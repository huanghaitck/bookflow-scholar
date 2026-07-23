from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bookflow.io_utils import atomic_write_json, atomic_write_jsonl
from bookflow.translation_cache import cache_fingerprint
from bookflow.translation_runner import TranslationRunner

ROOT = Path(".")


class EchoProvider:
    def __init__(self): self.calls = 0
    def translate_batch(self, units):
        self.calls += 1
        return [{"translation_unit_id": u["translation_unit_id"], "source_object_id": u["source_object_id"], "translated_text": "译文 " + u["source_text"]} for u in units]


def setup_root(tmp_path):
    base = tmp_path / "data/fullbook/multilingual"
    (base / "units").mkdir(parents=True)
    shutil.copy2(ROOT / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl", base / "units/translation_units_zh-Hans_v1.jsonl")
    units = [json.loads(x) for x in (base / "units/translation_units_zh-Hans_v1.jsonl").read_text("utf-8").splitlines() if x.strip()]
    states = [{"translation_unit_id": u["translation_unit_id"], "source_text_sha256": u["source_text_sha256"], "status": u["translation_status"], "attempts": 0, "last_error": None} for u in units]
    atomic_write_jsonl(base / "state/translation_state_zh-Hans_v1.jsonl", states)
    for rel in ["documents/multilingual_book_document_zh-Hans_v1.json", "multilingual_book_manifest_v1.json", "reports/multilingual_validation_zh-Hans_v1.json"]:
        source = ROOT / "data/fullbook/multilingual" / rel
        target = base / rel; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    atomic_write_json(base / "documents/multilingual_translation_overlay_zh-Hans_v1.json", {"translations": {}})
    atomic_write_json(base / "translation_manifest_zh-Hans_v1.json", {"user_production_api_calls": 0})
    atomic_write_json(base / "checkpoints/translation_zh-Hans_production.json", {"completed_unit_ids": [], "status": "resumable", "user_production_api_calls": 0})
    return units, states


def state_rows(root):
    return [json.loads(x) for x in (root / "data/fullbook/multilingual/state/translation_state_zh-Hans_v1.jsonl").read_text("utf-8").splitlines() if x.strip()]


def seed_valid_cache(root, unit, states, *, state_status="validated", checkpoint=True):
    runner = TranslationRunner(root)
    spec = runner._spec(unit, "deepseek_translation", "test-model")
    fp = cache_fingerprint(spec)
    record = {"translation_unit_id": unit["translation_unit_id"], "source_object_id": unit["source_object_id"], "translated_text": "安全译文", "status": "translated", "validated": True, "source_text_sha256": unit["source_text_sha256"], "fingerprint": fp, "provider": "deepseek_translation", "model": "test-model"}
    runner.cache.put(fp, record)
    row = next(s for s in states if s["translation_unit_id"] == unit["translation_unit_id"])
    row.update(status=state_status, cache_fingerprint=fp, attempts=1, last_error=None)
    atomic_write_jsonl(runner.state_path, states)
    if checkpoint: atomic_write_json(runner.checkpoint_path, {"completed_unit_ids": [unit["translation_unit_id"]], "status": "resumable", "user_production_api_calls": 1})
    return runner, fp


@pytest.fixture
def committed(tmp_path):
    units, _ = setup_root(tmp_path); provider = EchoProvider(); runner = TranslationRunner(tmp_path, provider)
    initial_pending = sum(u["translation_status"] == "pending" for u in units)
    runner.run(provider_name="deepseek_translation", model="test-model", max_units=1, batch_size=1, resume=True)
    uid = next(u["translation_unit_id"] for u in units if u["translation_status"] == "pending")
    return tmp_path, runner, provider, uid, initial_pending


def test_success_decrements_pending_by_one(committed):
    assert committed[1].status()["status_counts"]["pending"] == committed[4] - 1


def test_success_has_one_validated(committed):
    assert committed[1].status()["status_counts"]["validated"] == 1


def test_overlay_contains_validated_unit(committed):
    root, _, _, uid, _ = committed
    overlay = json.loads((root / "data/fullbook/multilingual/documents/multilingual_translation_overlay_zh-Hans_v1.json").read_text("utf-8"))
    assert uid in overlay["translations"]


def test_manifest_and_validation_counts_are_synchronized(committed):
    root = committed[0]
    manifest = json.loads((root / "data/fullbook/multilingual/multilingual_book_manifest_v1.json").read_text("utf-8"))
    validation = json.loads((root / "data/fullbook/multilingual/reports/multilingual_validation_zh-Hans_v1.json").read_text("utf-8"))
    assert manifest["status_counts"]["pending"] == committed[4] - 1 and manifest["validated_translation_count"] == 1
    assert validation["counts"]["status_counts"] == manifest["status_counts"] and validation["validation_passed"]


def test_translate_plan_returns_remaining_pending(committed):
    assert committed[1].plan()["unit_count"] == committed[4] - 1


def test_status_immediately_reads_current_state(committed):
    status = committed[1].status()
    assert status["pending"] == committed[4] - 1 and status["validated"] == 1


def test_user_api_calls_is_one_and_codex_zero(committed):
    status = committed[1].status()
    assert status["user_production_api_calls"] == 1 and status["codex_api_calls"] == 0


def test_cache_with_interrupted_overlay_is_reconciled_without_provider(tmp_path):
    units, states = setup_root(tmp_path); unit = next(u for u in units if u["translation_status"] == "pending")
    runner, _ = seed_valid_cache(tmp_path, unit, states)
    class Forbidden:
        def translate_batch(self, units): raise AssertionError("provider called")
    runner.provider = Forbidden(); report = runner.reconcile()
    overlay = json.loads((tmp_path / "data/fullbook/multilingual/documents/multilingual_translation_overlay_zh-Hans_v1.json").read_text("utf-8"))
    assert report["validated"] == 1 and unit["translation_unit_id"] in overlay["translations"]


def test_checkpoint_completed_but_state_pending_recovers_from_cache(tmp_path):
    units, states = setup_root(tmp_path); unit = next(u for u in units if u["translation_status"] == "pending")
    runner, _ = seed_valid_cache(tmp_path, unit, states, state_status="pending")
    runner.reconcile(); row = next(s for s in state_rows(tmp_path) if s["translation_unit_id"] == unit["translation_unit_id"])
    assert row["status"] == "validated"


def test_state_validated_overlay_missing_recovers_from_cache(tmp_path):
    units, states = setup_root(tmp_path); unit = next(u for u in units if u["translation_status"] == "pending")
    runner, _ = seed_valid_cache(tmp_path, unit, states, checkpoint=False)
    runner.reconcile(); doc = json.loads((tmp_path / "data/fullbook/multilingual/documents/multilingual_book_document_zh-Hans_v1.json").read_text("utf-8"))
    assert unit["translation_unit_id"] in doc["validated_translation_unit_ids"]


def test_missing_valid_cache_revokes_completed_and_marks_retryable(tmp_path):
    units, states = setup_root(tmp_path); unit = next(u for u in units if u["translation_status"] == "pending")
    row = next(s for s in states if s["translation_unit_id"] == unit["translation_unit_id"]); row["status"] = "validated"
    atomic_write_jsonl(TranslationRunner(tmp_path).state_path, states)
    atomic_write_json(TranslationRunner(tmp_path).checkpoint_path, {"completed_unit_ids": [unit["translation_unit_id"]], "status": "resumable"})
    TranslationRunner(tmp_path).reconcile(); row = next(s for s in state_rows(tmp_path) if s["translation_unit_id"] == unit["translation_unit_id"])
    assert row["status"] == "failed_retryable"


def test_checkpoint_is_committed_last_with_same_volume_atomic_replaces(committed):
    root = committed[0]; assert not list((root / "data/fullbook/multilingual").rglob("*.tmp"))
    checkpoint = root / "data/fullbook/multilingual/checkpoints/translation_zh-Hans_production.json"
    assert checkpoint.drive == (root / "data/fullbook/multilingual/state/translation_state_zh-Hans_v1.jsonl").drive


def test_secret_not_present_in_outputs(committed):
    root = committed[0]
    text = "".join(p.read_text("utf-8") for p in (root / "data/fullbook/multilingual").rglob("*.json*"))
    assert "Authorization: Bearer" not in text and "API_SECRET_SENTINEL" not in text


def test_reconciliation_does_not_change_unit_classifications(committed):
    units = [json.loads(x) for x in (committed[0] / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl").read_text("utf-8").splitlines() if x.strip()]
    assert sum(u["translation_status"] == "pending" for u in units) == committed[4]
