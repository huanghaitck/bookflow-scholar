from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookflow.cli import app
from bookflow.providers.config import load_provider_config
from bookflow.translation_runner import TranslationRunner

ROOT = Path(".")
CLI = CliRunner()


def copy_units(tmp_path):
    target = tmp_path / "data/fullbook/multilingual/units"
    target.mkdir(parents=True)
    target.joinpath("translation_units_zh-Hans_v1.jsonl").write_bytes(
        (ROOT / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl").read_bytes()
    )


class EchoProvider:
    calls = 0
    def health_check(self): return {"ok": True}
    def translate_batch(self, units):
        self.calls += 1
        return [{"translation_unit_id": u["translation_unit_id"], "source_object_id": u["source_object_id"], "translated_text": "译文 " + u["source_text"]} for u in units]


def local_config(tmp_path, *, allow=True, key_env="PHASE12_TEST_KEY"):
    path = tmp_path / "providers.yaml"
    path.write_text(f"""allow_real_api: {str(allow).lower()}
active_translation_provider: deepseek_translation
active_vision_provider: glm_vision
providers:
  deepseek_translation:
    type: openai_compatible
    base_url: https://deepseek.invalid/v1
    model: deepseek-test
    api_key_env: {key_env}
  glm_vision:
    type: openai_compatible
    base_url: https://glm.invalid/v1
    model: glm-vision-test
    api_key_env: GLM_TEST_KEY
""", "utf-8")
    return path


def test_translate_defaults_to_dry_run():
    result = CLI.invoke(app, ["translate", "--max-units", "1"])
    assert result.exit_code == 0 and '"dry_run": true' in result.stdout and '"api_calls": 0' in result.stdout


def test_real_execution_rejects_allow_false(tmp_path):
    result = CLI.invoke(app, ["translate", "--config", str(local_config(tmp_path, allow=False)), "--no-dry-run", "--yes"])
    assert result.exit_code == 2 and "allow_real_api=true" in result.stdout


def test_missing_no_dry_run_never_requires_key(tmp_path):
    result = CLI.invoke(app, ["translate", "--config", str(local_config(tmp_path)), "--max-units", "1"])
    assert result.exit_code == 0 and '"api_calls": 0' in result.stdout


def test_real_execution_rejects_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PHASE12_TEST_KEY", raising=False)
    result = CLI.invoke(app, ["translate", "--config", str(local_config(tmp_path)), "--no-dry-run", "--yes"])
    assert result.exit_code == 2 and "environment variable is missing" in result.stdout


def test_provider_config_and_roles_are_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_URL_TEST", "https://resolved.invalid/v1")
    path = local_config(tmp_path)
    cfg = load_provider_config(path)
    assert cfg["active_translation_provider"] == "deepseek_translation"
    assert cfg["active_vision_provider"] == "glm_vision"
    assert cfg["providers"]["deepseek_translation"]["model"] != cfg["providers"]["glm_vision"]["model"]
    assert all("api_key" not in provider for provider in cfg["providers"].values())


def test_pending_only_queue_reflects_completed_state():
    plan = TranslationRunner(ROOT).plan()
    states = [json.loads(line) for line in (ROOT / "data/fullbook/multilingual/state/translation_state_zh-Hans_v1.jsonl").read_text("utf-8").splitlines() if line]
    assert plan["unit_count"] == sum(state["status"] == "pending" for state in states)
    assert set(plan["unit_type_counts"]) <= {"appendix_element"}
    assert plan["retranslated_existing_main_text"] == 0
    assert plan["preserve_source_queued"] == 0
    assert plan["blocked_source_queued"] == 0
    assert plan["excluded_status_counts"]["validated"] == sum(state["status"] == "validated" for state in states)
    assert plan["excluded_status_counts"]["reused_frozen"] == 971
    assert plan["excluded_status_counts"]["preserve_source"] == 334
    assert plan["excluded_status_counts"]["blocked_by_source_quality"] == 7


def test_non_pending_status_filter_rejected():
    with pytest.raises(ValueError, match="pending-only"):
        TranslationRunner(ROOT).plan(status_filter="preserve_source")


def test_first_batch_gate_and_cache_hit(tmp_path):
    copy_units(tmp_path); provider = EchoProvider(); runner = TranslationRunner(tmp_path, provider)
    first = runner.run(provider_name="deepseek_translation", model="test", max_units=2, batch_size=2, resume=False)
    second = runner.run(provider_name="deepseek_translation", model="test", max_units=2, batch_size=2, resume=False)
    assert first["validated"] == 2 and first["api_calls"] == 1
    assert second["validated"] == 2 and second["api_calls"] == 1 and provider.calls == 2


def test_malformed_first_batch_stops_without_state(tmp_path):
    copy_units(tmp_path)
    class Bad:
        def translate_batch(self, units): return [{"bad": True}]
    runner = TranslationRunner(tmp_path, Bad())
    with pytest.raises(ValueError): runner.run(provider_name="deepseek_translation", model="test", max_units=1, resume=False)
    assert not runner.state_path.exists()


def test_interrupt_and_resume_without_duplicate_charge(tmp_path):
    copy_units(tmp_path); provider = EchoProvider(); runner = TranslationRunner(tmp_path, provider)
    first = runner.run(provider_name="deepseek_translation", model="test", max_units=3, batch_size=1, interrupt_after=1, resume=True)
    second = runner.run(provider_name="deepseek_translation", model="test", max_units=3, batch_size=1, resume=True)
    assert first["validated"] == 1 and second["validated"] == 2 and provider.calls == 3


def test_secret_never_persisted(tmp_path):
    copy_units(tmp_path); runner = TranslationRunner(tmp_path, EchoProvider())
    runner.run(provider_name="deepseek_translation", model="test", max_units=1, resume=False)
    assert "API_SECRET_SENTINEL" not in "".join(p.read_text("utf-8") for p in (tmp_path / "data/fullbook/multilingual").rglob("*.json*"))


def test_local_config_is_gitignored():
    assert "config/providers.local.yaml" in (ROOT / ".gitignore").read_text("utf-8")


def test_project_local_provider_config_uses_one_env_loader():
    cfg = load_provider_config(ROOT / "config/providers.local.yaml")
    assert cfg["active_translation_provider"] == "deepseek_translation"
    assert cfg["active_vision_provider"] == "glm_vision"
    assert cfg["providers"]["deepseek_translation"]["base_url"] == "https://api.deepseek.com"
    assert cfg["providers"]["deepseek_translation"]["model"] == "deepseek-v4-pro"
    assert cfg["providers"]["deepseek_translation"]["api_key_available"] is True
    assert cfg["providers"]["glm_vision"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert cfg["providers"]["glm_vision"]["model"] == "glm-4.6v"
    assert cfg["providers"]["glm_vision"]["api_key_available"] is True
