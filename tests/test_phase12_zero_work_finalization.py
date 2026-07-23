from __future__ import annotations

import json
from pathlib import Path
import yaml

from typer.testing import CliRunner

from bookflow.cli import app
from bookflow.io_utils import atomic_write_json, atomic_write_jsonl
from bookflow.translation_cache import cache_fingerprint
from bookflow.translation_runner import TranslationRunner


def make_root(tmp_path, status="validated", calls=43):
    base = tmp_path / "data/fullbook/multilingual"
    unit = {"translation_unit_id": "tu_test", "source_object_id": "obj", "source_object_type": "book_metadata", "source_language": "en", "target_language": "zh-Hans", "source_text": "Title", "source_text_sha256": "sha", "translation_policy": "translate_title", "translation_status": "pending"}
    atomic_write_jsonl(base / "units/translation_units_zh-Hans_v1.jsonl", [unit])
    state = {"translation_unit_id": "tu_test", "source_text_sha256": "sha", "status": status, "attempts": 1 if status == "validated" else 0, "last_error": None}
    runner = TranslationRunner(tmp_path)
    completed = []
    if status == "validated":
        fp = cache_fingerprint(runner._spec(unit, "deepseek_translation", "model"))
        runner.cache.put(fp, {"translation_unit_id": "tu_test", "source_object_id": "obj", "translated_text": "标题", "source_text_sha256": "sha", "fingerprint": fp, "provider": "deepseek_translation", "model": "model", "validated": True})
        state["cache_fingerprint"] = fp; completed = ["tu_test"]
    atomic_write_jsonl(runner.state_path, [state])
    atomic_write_json(base / "documents/multilingual_book_document_zh-Hans_v1.json", {})
    atomic_write_json(base / "multilingual_book_manifest_v1.json", {})
    atomic_write_json(base / "reports/multilingual_validation_zh-Hans_v1.json", {"checks": {}})
    atomic_write_json(base / "translation_manifest_zh-Hans_v1.json", {"user_production_api_calls": calls})
    atomic_write_json(runner.checkpoint_path, {"completed_unit_ids": completed, "status": "resumable", "user_production_api_calls": calls})
    return runner


def test_pending_zero_resume_closes_checkpoint(tmp_path):
    runner = make_root(tmp_path); result = runner.run(provider_name="unused", model="unused", resume=True)
    checkpoint = json.loads(runner.checkpoint_path.read_text("utf-8"))
    assert result["already_completed"] and result["api_calls"] == 0
    assert checkpoint["status"] == "completed" and checkpoint["completed_at"] and checkpoint["next_action"] == "build_first_book_releases"


def test_completed_checkpoint_repeated_resume_is_idempotent(tmp_path):
    runner = make_root(tmp_path); first = runner.run(provider_name="unused", model="unused"); before = runner.checkpoint_path.read_bytes()
    second = runner.run(provider_name="unused", model="unused"); after = runner.checkpoint_path.read_bytes()
    assert first["already_completed"] and second["already_completed"] and before == after


def test_zero_units_never_calls_provider(tmp_path):
    runner = make_root(tmp_path)
    class Forbidden:
        def translate_batch(self, units): raise AssertionError("provider called")
    runner.provider = Forbidden(); assert runner.run(provider_name="unused", model="unused")["api_calls"] == 0


def test_api_count_stays_43_and_completed_ids_unchanged(tmp_path):
    runner = make_root(tmp_path); before = json.loads(runner.checkpoint_path.read_text("utf-8"))["completed_unit_ids"]
    status = runner.status(); after = json.loads(runner.checkpoint_path.read_text("utf-8"))["completed_unit_ids"]
    assert status["user_production_api_calls"] == 43 and status["codex_api_calls"] == 0 and before == after


def test_pending_greater_than_zero_remains_resumable(tmp_path):
    runner = make_root(tmp_path, status="pending")
    assert runner.status()["production_checkpoint_status"] == "resumable"


def test_failed_retryable_blocks_completion(tmp_path):
    runner = make_root(tmp_path, status="failed_retryable")
    report = runner.status(); assert report["production_checkpoint_status"] == "resumable" and report["status_counts"]["failed_retryable"] == 1


def test_status_reads_latest_completed_checkpoint(tmp_path):
    runner = make_root(tmp_path); runner.status(); second = TranslationRunner(tmp_path).status()
    assert second["production_checkpoint_status"] == "completed" and second["next_action"] == "build_first_book_releases"


def test_cli_zero_work_short_circuits_before_provider_factory(tmp_path, monkeypatch):
    make_root(tmp_path)
    config = tmp_path / "providers.yaml"
    config.write_text("allow_real_api: true\nactive_translation_provider: deepseek\nproviders:\n  deepseek:\n    type: openai_compatible\n    model: m\n    api_key_env: ZERO_WORK_KEY\n", "utf-8")
    monkeypatch.setenv("ZERO_WORK_KEY", "secret-not-serialized")
    monkeypatch.setattr("bookflow.cli.project_root", lambda: tmp_path)
    monkeypatch.setattr("bookflow.cli._translation_context", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider factory called")))
    result = CliRunner().invoke(app, ["resume", "--config", str(config), "--yes"])
    assert result.exit_code == 0 and '"already_completed": true' in result.stdout and '"api_calls": 0' in result.stdout


def test_phase12r_remains_completed_after_later_authorized_phases():
    state = yaml.safe_load(Path("docs/CURRENT_PROJECT_STATE.yaml").read_text("utf-8"))
    assert state["phase_12r"]["status"] == "completed"
    assert state["phase_12r"]["frozen_hashes_verified"] is True
    assert state["phase_12r"]["old_releases_unchanged"] is True
    assert state["current_phase"] == "phase_13_6"
    assert state["phase_13_6"]["status"] in {"awaiting_ui_selection", "ready_for_ui_selection"}
    assert state["next_phase"] == "phase_14b_pending_ui_selection"
