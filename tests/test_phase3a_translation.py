from __future__ import annotations

from pathlib import Path
import importlib.util
import json

import yaml
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bookflow.cli import app

from bookflow.paths import load_settings, project_root
from bookflow.io_utils import load_json
from bookflow.translation_provider import TranslationProviderResponse


ROOT = project_root()
RUNNER = CliRunner()


def _settings():
    return load_settings(ROOT / "config" / "settings.example.yaml")


def test_phase3a_translation_configuration_is_explicit() -> None:
    settings = _settings()
    assert settings.translation_provider == "deepseek"
    assert settings.translation_base_url == "https://api.deepseek.com"
    assert settings.translation_model == "deepseek-v4-pro"
    assert settings.translation_api_key_env == "DEEPSEEK_API_KEY"
    assert settings.translation_source_language == "en"
    assert settings.translation_target_language == "zh-Hans"


def test_phase3a_safety_configuration_is_fail_closed() -> None:
    settings = _settings()
    assert settings.translation_thinking_mode == "disabled"
    assert settings.translation_response_format_json_object is True
    assert settings.translation_automatic_retry is False
    assert settings.translation_maximum_real_calls == 5
    assert settings.translation_maximum_model_list_calls == 1
    assert settings.translation_maximum_cash_cost_cny == 1.0
    assert settings.translation_enabled is False
    assert settings.automatic_phase_advance is False


def test_phase3a_prompt_and_language_profile_are_configured_paths() -> None:
    settings = _settings()
    assert settings.translation_prompt_path == "prompts/translation_en_zh_v1.md"
    assert settings.translation_language_profile_path == "language_profiles/zh-Hans.yaml"


def test_phase3a_public_prices_are_configured_in_cny() -> None:
    settings = _settings()
    assert settings.translation_input_cache_hit_price_cny_per_million_tokens == 0.025
    assert settings.translation_input_cache_miss_price_cny_per_million_tokens == 3.0
    assert settings.translation_output_price_cny_per_million_tokens == 6.0
    assert settings.translation_pricing_checked_date == "2026-07-15"


def test_translation_prompt_requires_target_only_json_contract() -> None:
    prompt = (ROOT / "prompts" / "translation_en_zh_v1.md").read_text(encoding="utf-8")
    assert "只翻译" in prompt
    assert "source_text" in prompt
    assert "context_before_text" in prompt
    assert "不得作为额外内容混入当前单元的translation" in prompt
    assert "chapter_title" in prompt
    assert "section_title" in prompt
    assert "running_header" in prompt
    assert '"block_type"' in prompt
    assert '"target_block_id"' in prompt
    assert '"uncertain_terms"' in prompt
    assert "JSON" in prompt
    assert "```" not in prompt


def test_language_profile_is_versioned_and_preserves_historical_voice() -> None:
    raw = yaml.safe_load(
        (ROOT / "language_profiles" / "zh-Hans.yaml").read_text(encoding="utf-8")
    )
    assert raw["version"] == "zh-Hans-v1"
    assert raw["source_language"] == "en"
    assert raw["target_language"] == "zh-Hans"
    assert raw["preserve_historical_travel_voice"] is True
    assert raw["modernize_historical_romanization"] is False
    assert raw["silently_sanitize_offensive_terms"] is False


def test_phase3a_translation_modules_exist() -> None:
    assert importlib.util.find_spec("bookflow.translation_schemas") is not None
    assert importlib.util.find_spec("bookflow.translation_provider") is not None
    assert importlib.util.find_spec("bookflow.translation_pipeline") is not None


def test_translation_output_schema_rejects_empty_or_extra_content() -> None:
    from bookflow.translation_schemas import TranslationModelPayload

    valid = {
        "target_block_id": "block-1",
        "block_type": "body",
        "translation": "译文",
        "uncertain_terms": [],
        "historical_terms": [],
        "warnings": [],
    }
    assert TranslationModelPayload.model_validate(valid).translation == "译文"
    with pytest.raises(ValidationError):
        TranslationModelPayload.model_validate({**valid, "translation": "  "})
    with pytest.raises(ValidationError):
        TranslationModelPayload.model_validate({**valid, "source_text": "must not return"})


def test_translation_request_keeps_target_and_context_separate() -> None:
    from bookflow.translation_schemas import TranslationRequestPayload

    payload = TranslationRequestPayload(
        translation_unit_id="translation_block-1",
        target_block_id="block-1",
        block_type="body",
        source_text="Translate only this.",
        chapter_id="chapter-1",
        section_id="section-1",
        chapter_title_block_id="chapter-1",
        section_title_block_id="section-1",
        chapter_title_context_source="CHAPTER I",
        chapter_title_context_translation=None,
        section_title_context_source="THE CALL",
        section_title_context_translation=None,
        context_before_block_ids=["before-1"],
        context_after_block_ids=["after-1"],
        context_before_text="Before only.",
        context_after_text="After only.",
        source_pages=[1, 2],
        source_language="en",
        target_language="zh-Hans",
        translate_target_only=True,
        glossary=[],
        translation_profile={"version": "zh-Hans-v1"},
    )
    assert payload.source_text == "Translate only this."
    assert payload.context_before_text == "Before only."
    assert payload.translate_target_only is True
    assert payload.chapter_title_context_source == "CHAPTER I"


class _FakeResponse:
    def __init__(self, value: dict):
        self.value = value

    def model_dump(self, mode: str = "json") -> dict:
        return self.value


class _FakeCompletions:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **kwargs):
        self.owner.request = kwargs
        return _FakeResponse(
            {
                "id": "request-1",
                "model": kwargs["model"],
                "choices": [{"message": {"content": '{"target_block_id":"block-1","block_type":"body","translation":"译文","uncertain_terms":[],"historical_terms":[],"warnings":[]}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )


class _FakeModels:
    def list(self):
        return _FakeResponse({"object": "list", "data": [{"id": "configured-model"}]})


class _FakeClient:
    last = None

    def __init__(self, **kwargs):
        type(self).last = self
        self.init_kwargs = kwargs
        self.request = None
        self.models = _FakeModels()
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(self)


def test_translation_provider_uses_configured_model_disabled_thinking_and_json() -> None:
    from bookflow.translation_provider import DeepSeekOpenAICompatibleProvider

    provider = DeepSeekOpenAICompatibleProvider(
        api_key="not-recorded",
        base_url="https://example.invalid",
        timeout_seconds=30,
        client_factory=_FakeClient,
    )
    response = provider.translate_one(
        model="configured-model",
        system_prompt="Return json.",
        user_payload={"target_block_id": "block-1", "source_text": "Text"},
        max_output_tokens=100,
        temperature=0,
        thinking_mode="disabled",
    )
    client = _FakeClient.last
    assert client.init_kwargs["base_url"] == "https://example.invalid"
    assert client.init_kwargs["max_retries"] == 0
    assert client.request["model"] == "configured-model"
    assert client.request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert client.request["response_format"] == {"type": "json_object"}
    assert client.request["temperature"] == 0
    assert response.usage["total_tokens"] == 15


def test_model_list_provider_returns_only_model_ids() -> None:
    from bookflow.translation_provider import DeepSeekOpenAICompatibleProvider

    provider = DeepSeekOpenAICompatibleProvider(
        api_key="not-recorded",
        base_url="https://example.invalid",
        timeout_seconds=30,
        client_factory=_FakeClient,
    )
    assert provider.list_model_ids() == {"configured-model"}


def test_representative_selection_has_title_and_four_distinct_body_types() -> None:
    from bookflow.translation_pipeline import select_representative_blocks

    selected = select_representative_blocks(_settings(), root=ROOT)
    assert len(selected) == 5
    assert len({item.logical_block_id for item in selected}) == 5
    assert {item.selection_type for item in selected} == {
        "structural_title",
        "ordinary_single_page",
        "cross_page",
        "rhetorical_long_form",
        "proper_names_or_historical_voice",
    }
    title = next(item for item in selected if item.selection_type == "structural_title")
    assert title.block_type in {"chapter_title", "section_title"}
    assert all(item.translation_ready for item in selected)
    assert all(not item.unresolved_boundaries for item in selected)
    assert all(item.logical_block_id != "logical2_62d4a069db871f2cc20b" for item in selected)


def test_translation_preflight_is_offline_and_blocks_missing_key() -> None:
    from bookflow.translation_pipeline import translation_preflight

    report = translation_preflight(
        _settings(),
        root=ROOT,
        key_status_resolver=lambda settings, root: (False, None),
    )
    assert report.api_called is False
    assert report.target_block_count == 5
    assert report.ready_for_real_call is False
    assert any("API key" in blocker for blocker in report.blockers)
    assert report.estimated_cost_upper_cny <= 1.0


def test_translation_preflight_blocks_cost_over_hard_limit() -> None:
    from bookflow.translation_pipeline import translation_preflight

    settings = _settings().model_copy(
        update={"translation_output_price_cny_per_million_tokens": 1000.0}
    )
    report = translation_preflight(
        settings,
        root=ROOT,
        key_status_resolver=lambda settings, root: (True, "DEEPSEEK_API_KEY"),
    )
    assert report.estimated_cost_upper_cny > 1.0
    assert report.ready_for_real_call is False
    assert any("cash" in blocker.lower() for blocker in report.blockers)


def test_build_request_rejects_incomplete_page11_block() -> None:
    from bookflow.translation_pipeline import build_translation_request

    settings = _settings()
    with pytest.raises(ValueError, match="translation_ready"):
        build_translation_request(
            "logical2_62d4a069db871f2cc20b",
            settings,
            root=ROOT,
        )


def test_translation_request_context_is_read_only_and_stays_in_chapter() -> None:
    from bookflow.translation_pipeline import build_translation_request, select_representative_blocks

    settings = _settings()
    candidate = next(
        item for item in select_representative_blocks(settings, root=ROOT)
        if item.selection_type == "cross_page"
    )
    payload = build_translation_request(candidate.logical_block_id, settings, root=ROOT)
    assert payload.target_block_id == candidate.logical_block_id
    assert payload.translate_target_only is True
    assert payload.context_before_text != payload.source_text
    assert payload.context_after_text != payload.source_text
    assert payload.block_type == "body"
    assert payload.chapter_title_context_source == "CHAPTER I"
    assert payload.section_title_context_source == "THE CALL OF THE RED GODS"
    assert payload.chapter_title_context_translation is None
    assert payload.context_before_block_ids == [candidate.context_before_block_id]
    assert payload.translation_profile["version"] == "zh-Hans-v1"


def _phase3_settings(tmp_path: Path):
    return _settings().model_copy(
        update={
            "translation_request_directory": str(tmp_path / "requests"),
            "translation_raw_directory": str(tmp_path / "raw"),
            "translation_normalized_directory": str(tmp_path / "normalized"),
            "translation_cache_directory": str(tmp_path / "cache"),
            "translation_usage_directory": str(tmp_path / "usage"),
            "translation_report_directory": str(tmp_path / "reports"),
            "translation_derived_document_path": str(tmp_path / "phase3a.json"),
            "translation_diagnostic_markdown_path": str(tmp_path / "phase3a.md"),
            "translation_diagnostic_docx_path": str(tmp_path / "phase3a.docx"),
        }
    )


class _ScriptedProvider:
    def __init__(self, script=None, *, model_available: bool = True):
        self.script = script or {}
        self.model_available = model_available
        self.model_calls = 0
        self.translation_calls = 0

    def list_model_ids(self):
        self.model_calls += 1
        return {"deepseek-v4-pro"} if self.model_available else {"some-other-model"}

    def translate_one(self, *, model, system_prompt, user_payload, **kwargs):
        self.translation_calls += 1
        block_id = user_payload["target_block_id"]
        scripted = self.script.get(block_id)
        if callable(scripted):
            content = scripted(user_payload)
        elif scripted is not None:
            content = scripted
        else:
            content = json.dumps(
                {
                    "target_block_id": block_id,
                    "block_type": user_payload["block_type"],
                    "translation": "忠实测试译文。" + "译" * max(40, len(user_payload["source_text"]) // 3),
                    "uncertain_terms": [],
                    "historical_terms": [],
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        return TranslationProviderResponse(
            raw_response={
                "id": f"request-{self.translation_calls}",
                "model": model,
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
            content=content,
            request_id=f"request-{self.translation_calls}",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            response_model=model,
        )


def _run_mock(tmp_path: Path, provider: _ScriptedProvider):
    from bookflow.translation_pipeline import run_translation_sample

    return run_translation_sample(
        _phase3_settings(tmp_path),
        root=ROOT,
        allow_api=True,
        confirm_five_calls=True,
        key_status_resolver=lambda settings, root: (True, "DEEPSEEK_API_KEY"),
        key_loader=lambda settings, root: ("local-test-key", "DEEPSEEK_API_KEY"),
        provider_factory=lambda **kwargs: provider,
    )


def test_translation_dry_run_and_missing_confirmation_make_no_calls(tmp_path: Path) -> None:
    from bookflow.translation_pipeline import run_translation_sample

    provider = _ScriptedProvider()
    dry = run_translation_sample(
        _phase3_settings(tmp_path),
        root=ROOT,
        allow_api=False,
        confirm_five_calls=False,
        key_status_resolver=lambda settings, root: (True, "DEEPSEEK_API_KEY"),
        provider_factory=lambda **kwargs: provider,
    )
    assert dry.api_calls == 0
    assert provider.model_calls == 0
    assert provider.translation_calls == 0
    with pytest.raises(PermissionError):
        run_translation_sample(
            _phase3_settings(tmp_path),
            root=ROOT,
            allow_api=True,
            confirm_five_calls=False,
            key_status_resolver=lambda settings, root: (True, "DEEPSEEK_API_KEY"),
            provider_factory=lambda **kwargs: provider,
        )


def test_mock_five_block_run_saves_raw_normalized_usage_and_derived_data(tmp_path: Path) -> None:
    provider = _ScriptedProvider()
    result = _run_mock(tmp_path, provider)
    assert result.api_calls == 5
    assert result.model_list_calls == 1
    assert result.failed == 0
    assert result.retries == 0
    assert len(result.results) == 5
    assert {item.target_block_id for item in result.results} == set(result.selected_block_ids)
    assert all(Path(item.raw_response_path).is_file() for item in result.results)
    assert Path(result.derived_document_path).is_file()
    assert Path(result.diagnostic_markdown_path).is_file()
    assert Path(result.diagnostic_docx_path).is_file()
    assert result.strict_export_ready is True
    assert not list(tmp_path.rglob("*final*"))


def test_second_identical_run_is_five_cache_hits_and_zero_new_calls(tmp_path: Path) -> None:
    first_provider = _ScriptedProvider()
    first = _run_mock(tmp_path, first_provider)
    second_provider = _ScriptedProvider()
    second = _run_mock(tmp_path, second_provider)
    assert first.api_calls == 5
    assert second.api_calls == 0
    assert second.model_list_calls == 0
    assert second.cache_hits == 5
    assert second_provider.model_calls == 0
    assert second_provider.translation_calls == 0


@pytest.mark.parametrize(
    "bad_content",
    [
        "",
        "not-json",
        '{"target_block_id":"wrong","block_type":"body","translation":"译文","uncertain_terms":[],"historical_terms":[],"warnings":[]}',
    ],
)
def test_empty_invalid_or_wrong_id_response_fails_without_retry(tmp_path: Path, bad_content: str) -> None:
    from bookflow.translation_pipeline import select_representative_blocks

    block_id = select_representative_blocks(_settings(), root=ROOT)[0].logical_block_id
    provider = _ScriptedProvider({block_id: bad_content})
    result = _run_mock(tmp_path, provider)
    assert result.failed == 1
    assert result.retries == 0
    assert result.api_calls == 1
    assert provider.translation_calls == 1
    assert list((tmp_path / "raw").rglob("*.json"))
    assert not Path(tmp_path / "phase3a.json").exists()
    assert result.strict_export_ready is False


def test_source_or_context_repetition_is_rejected(tmp_path: Path) -> None:
    from bookflow.translation_pipeline import select_representative_blocks, build_translation_request

    settings = _settings()
    block_id = select_representative_blocks(settings, root=ROOT)[0].logical_block_id
    payload = build_translation_request(block_id, settings, root=ROOT)

    def leaked(_):
        return json.dumps(
            {
                "target_block_id": block_id,
                "block_type": _["block_type"],
                "translation": payload.context_after_text or payload.source_text,
                "uncertain_terms": [],
                "historical_terms": [],
                "warnings": [],
            },
            ensure_ascii=False,
        )

    result = _run_mock(tmp_path, _ScriptedProvider({block_id: leaked}))
    assert result.failed == 1
    assert result.api_calls == 1


def test_unavailable_configured_model_stops_before_translation(tmp_path: Path) -> None:
    provider = _ScriptedProvider(model_available=False)
    result = _run_mock(tmp_path, provider)
    assert result.model_list_calls == 1
    assert result.model_available is False
    assert result.api_calls == 0
    assert provider.translation_calls == 0


def test_persistent_five_call_limit_cannot_be_bypassed(tmp_path: Path) -> None:
    first = _run_mock(tmp_path, _ScriptedProvider())
    assert first.api_calls == 5
    cache_file = next((tmp_path / "cache").rglob("*.json"))
    cache_file.unlink()
    with pytest.raises(RuntimeError, match="limit"):
        _run_mock(tmp_path, _ScriptedProvider())


def test_original_master_document_is_not_modified_by_translation_sample(tmp_path: Path) -> None:
    import hashlib

    master = ROOT / "data" / "bilingual_document.json"
    before = hashlib.sha256(master.read_bytes()).hexdigest()
    _run_mock(tmp_path, _ScriptedProvider())
    after = hashlib.sha256(master.read_bytes()).hexdigest()
    assert before == after


def test_phase3a_derived_markdown_contains_targets_not_context(tmp_path: Path) -> None:
    from bookflow.translation_pipeline import build_translation_request

    result = _run_mock(tmp_path, _ScriptedProvider())
    derived = load_json(result.derived_document_path)
    markdown = Path(result.diagnostic_markdown_path).read_text(encoding="utf-8")
    assert len(derived["translations"]) == 5
    for item in derived["translations"]:
        assert item["source_text"] in markdown
        assert item["translation"] in markdown
        request = build_translation_request(item["target_block_id"], _settings(), root=ROOT)
        assert "context_before_text" not in item
        assert "context_after_text" not in item
        assert request.context_before_text not in item["translation"] if request.context_before_text else True
        assert request.context_after_text not in item["translation"] if request.context_after_text else True


def test_chapter_and_section_titles_build_independent_translation_units() -> None:
    from bookflow.translation_pipeline import build_translation_request

    settings = _settings()
    chapter = build_translation_request("logical2_9d714ef8c1fa496136e8", settings, root=ROOT)
    section = build_translation_request("logical2_a162834ad5c991557dd9", settings, root=ROOT)
    assert chapter.block_type == "chapter_title"
    assert chapter.source_text == "CHAPTER I"
    assert chapter.chapter_title_context_source is None
    assert section.block_type == "section_title"
    assert section.source_text == "THE CALL OF THE RED GODS"
    assert section.section_title_context_source is None


def test_running_headers_never_become_translation_units_or_duplicates() -> None:
    from bookflow.translation_pipeline import build_translation_units

    units = build_translation_units(_settings(), root=ROOT)
    assert units
    assert all(item.block_type not in {"running_header", "running_footer", "page_number", "decorative_text"} for item in units)
    ids = [item.target_block_id for item in units]
    assert len(ids) == len(set(ids))
    assert sum(item.source_text == "THE CALL OF THE RED GODS" for item in units) == 1
    assert all(item.source_text != "THE CHARM OF JAPAN" for item in units)


def test_chapter_title_translation_output_is_accepted_as_its_own_target(tmp_path: Path) -> None:
    from bookflow.io_utils import sha256_text
    from bookflow.translation_pipeline import _normalize_translation, build_translation_request

    settings = _settings()
    payload = build_translation_request("logical2_9d714ef8c1fa496136e8", settings, root=ROOT)
    raw_path = tmp_path / "raw.json"
    raw_path.write_text("{}", encoding="utf-8")
    content = json.dumps({
        "target_block_id": payload.target_block_id,
        "block_type": "chapter_title",
        "translation": "第一章",
        "uncertain_terms": [],
        "historical_terms": [],
        "warnings": [],
    }, ensure_ascii=False)
    normalized = _normalize_translation(
        content=content,
        payload=payload,
        settings=settings,
        fingerprint="fingerprint",
        prompt_sha256=sha256_text("prompt"),
        profile_sha256=sha256_text("profile"),
        raw_path=raw_path,
        usage=None,
        request_id=None,
    )
    assert normalized.block_type == "chapter_title"
    assert normalized.translation == "第一章"


def test_body_target_and_title_context_are_strictly_separate() -> None:
    from bookflow.translation_pipeline import build_translation_request, select_representative_blocks

    settings = _settings()
    body = next(item for item in select_representative_blocks(settings, root=ROOT) if item.block_type == "body")
    request = build_translation_request(body.logical_block_id, settings, root=ROOT)
    assert request.source_text not in {request.chapter_title_context_source, request.section_title_context_source}
    assert request.target_block_id not in {request.chapter_title_block_id, request.section_title_block_id}


def test_body_translation_repeating_title_context_is_rejected(tmp_path: Path) -> None:
    from bookflow.translation_pipeline import build_translation_request, select_representative_blocks

    settings = _settings()
    body = next(item for item in select_representative_blocks(settings, root=ROOT) if item.block_type == "body")
    request = build_translation_request(body.logical_block_id, settings, root=ROOT)
    leaked = json.dumps({
        "target_block_id": body.logical_block_id,
        "block_type": "body",
        "translation": request.section_title_context_source or request.chapter_title_context_source,
        "uncertain_terms": [],
        "historical_terms": [],
        "warnings": [],
    }, ensure_ascii=False)
    result = _run_mock(tmp_path, _ScriptedProvider({body.logical_block_id: leaked}))
    assert result.failed == 1
    assert result.strict_export_ready is False


def test_title_translation_is_written_by_block_id_to_same_markdown_and_word_source(tmp_path: Path) -> None:
    from docx import Document

    result = _run_mock(tmp_path, _ScriptedProvider())
    derived = load_json(result.derived_document_path)
    title = next(item for item in derived["translations"] if item["block_type"] in {"chapter_title", "section_title"})
    markdown = Path(result.diagnostic_markdown_path).read_text(encoding="utf-8")
    docx_text = "\n".join(p.text for p in Document(result.diagnostic_docx_path).paragraphs)
    assert title["target_block_id"] in {item["target_block_id"] for item in derived["translations"]}
    assert title["source_text"] in markdown and title["translation"] in markdown
    assert title["source_text"] in docx_text and title["translation"] in docx_text


def test_title_failure_blocks_all_strict_sample_exports(tmp_path: Path) -> None:
    from bookflow.translation_pipeline import select_representative_blocks

    title_id = next(item.logical_block_id for item in select_representative_blocks(_settings(), root=ROOT) if item.selection_type == "structural_title")
    bad = json.dumps({
        "target_block_id": title_id,
        "block_type": "body",
        "translation": "错误类型",
        "uncertain_terms": [],
        "historical_terms": [],
        "warnings": [],
    }, ensure_ascii=False)
    result = _run_mock(tmp_path, _ScriptedProvider({title_id: bad}))
    assert result.failed == 1
    assert result.strict_export_ready is False
    assert result.derived_document_path is None
    assert result.diagnostic_markdown_path is None
    assert result.diagnostic_docx_path is None


def test_phase3a_cli_exposes_preflight_sample_and_status_commands() -> None:
    result = RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "translation-preflight" in result.stdout
    assert "translation-sample" in result.stdout
    assert "translation-status" in result.stdout


def test_translation_sample_cli_defaults_to_zero_api_calls() -> None:
    result = RUNNER.invoke(
        app,
        ["translation-sample", "--config", str(ROOT / "config" / "settings.example.yaml")],
    )
    assert result.exit_code == 0
    assert "内容调用: 0" in result.stdout
    assert "GLM调用: 0" in result.stdout
    assert "正式final: 否" in result.stdout
