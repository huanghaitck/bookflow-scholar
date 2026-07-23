from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookflow.providers.openai_compatible import ProviderContractError, deepseek_transport
from bookflow.translation_provider import TranslationProviderResponse
from bookflow.translation_runner import TranslationRunner

ROOT = Path(".")
CONFIG = {"api_key_env": "UNUSED_TEST_KEY", "base_url": "https://example.invalid", "model": "deepseek-test"}


def unit(uid="u1", oid="o1", text="source"):
    return {"translation_unit_id": uid, "source_object_id": oid, "source_text": text, "placeholders": []}


class Client:
    def __init__(self, responses): self.responses, self.calls = list(responses), 0
    def translate_one(self, **kwargs):
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


def call(value, request=None):
    client = Client([value]); transport = deepseek_transport(CONFIG, client=client)
    return transport({"model": "deepseek-test", "units": [request or unit()]})


def response(content, finish="stop"):
    return TranslationProviderResponse(raw_response={"choices": [{"finish_reason": finish}]}, content=content, request_id="r", usage=None, response_model="deepseek-test")


def test_translate_one_plain_string_is_wrapped_with_request_ids():
    out = call("译文")
    assert out == {"translations": [{"translation_unit_id": "u1", "source_object_id": "o1", "translated_text": "译文", "status": "translated"}]}


def test_translate_one_single_dict_is_mapped():
    assert call({"translated_text": "译文", "status": "translated"})["translations"][0]["translation_unit_id"] == "u1"


def test_translate_one_complete_batch_dict_is_unwrapped():
    value = {"translations": [{"translation_unit_id": "u1", "source_object_id": "o1", "translated_text": "译文"}]}
    assert call(value)["translations"][0]["translated_text"] == "译文"


def test_model_content_handles_bom_whitespace_and_single_json_fence():
    value = response("\ufeff  ```json\n{\"translated_text\": \"译文\"}\n```  ")
    assert call(value)["translations"][0]["translated_text"] == "译文"


def test_empty_string_rejected():
    with pytest.raises(ProviderContractError, match="empty"):
        call("  ")


def test_missing_translated_text_rejected():
    with pytest.raises(ProviderContractError, match="translated_text"):
        call({"status": "translated"})


def test_returned_id_mismatch_rejected():
    with pytest.raises(ProviderContractError, match="translation_unit_id mismatch"):
        call({"translation_unit_id": "model-made-id", "translated_text": "译文"})


def copy_units(tmp_path):
    target = tmp_path / "data/fullbook/multilingual/units"; target.mkdir(parents=True)
    target.joinpath("translation_units_zh-Hans_v1.jsonl").write_bytes((ROOT / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl").read_bytes())


class PartialFailure:
    def translate_batch(self, units):
        return [{"translation_unit_id": units[0]["translation_unit_id"], "source_object_id": units[0]["source_object_id"], "translated_text": "译文 " + units[0]["source_text"]}]


def test_partial_batch_failure_writes_no_validated_cache(tmp_path):
    copy_units(tmp_path); runner = TranslationRunner(tmp_path, PartialFailure())
    with pytest.raises(ValueError, match="count mismatch"):
        runner.run(provider_name="deepseek_translation", model="test", max_units=2, batch_size=2, resume=False)
    assert not list((tmp_path / "data/fullbook/multilingual/cache").rglob("*.json"))
    assert not runner.state_path.exists()


class RetryProvider:
    def __init__(self): self.calls = 0
    def translate_batch(self, units):
        self.calls += 1
        if self.calls == 2: raise RuntimeError("temporary")
        return [{"translation_unit_id": u["translation_unit_id"], "source_object_id": u["source_object_id"], "translated_text": "译文 " + u["source_text"]} for u in units]


def test_later_batch_retry_succeeds(tmp_path):
    copy_units(tmp_path); provider = RetryProvider(); runner = TranslationRunner(tmp_path, provider)
    result = runner.run(provider_name="deepseek_translation", model="test", max_units=2, batch_size=1, max_retries=1, resume=False)
    assert result["validated"] == 2 and provider.calls == 3


def test_resume_does_not_repeat_successful_unit(tmp_path):
    copy_units(tmp_path); provider = RetryProvider(); runner = TranslationRunner(tmp_path, provider)
    runner.run(provider_name="deepseek_translation", model="test", max_units=1, batch_size=1, resume=True)
    calls = provider.calls
    result = runner.run(provider_name="deepseek_translation", model="test", max_units=1, batch_size=1, resume=True)
    assert result["validated"] == 0 and provider.calls == calls


def test_secret_is_not_written_to_failure_diagnostic(tmp_path):
    copy_units(tmp_path)
    class SecretFailure:
        def translate_batch(self, units): raise RuntimeError("Authorization: Bearer API_SECRET_SENTINEL")
    runner = TranslationRunner(tmp_path, SecretFailure())
    with pytest.raises(RuntimeError): runner.run(provider_name="deepseek_translation", model="test", max_units=1, resume=False)
    diagnostic = next((tmp_path / "data/fullbook/multilingual/diagnostics").glob("*.json")).read_text("utf-8")
    assert "API_SECRET_SENTINEL" not in diagnostic and "Authorization: Bearer" not in diagnostic


def test_pending_only_queue_reflects_completed_translation():
    plan = TranslationRunner(ROOT).plan()
    states = [json.loads(line) for line in (ROOT / "data/fullbook/multilingual/state/translation_state_zh-Hans_v1.jsonl").read_text("utf-8").splitlines() if line]
    assert plan["unit_count"] == sum(state["status"] == "pending" for state in states)
    assert set(plan["unit_type_counts"]) <= {"appendix_element"}
    assert plan["retranslated_existing_main_text"] == 0
