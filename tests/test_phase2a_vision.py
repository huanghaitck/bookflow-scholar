from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

import bookflow.page_pipeline as page_module
from bookflow.doctor import format_doctor_report, run_doctor
from bookflow.io_utils import load_json
from bookflow.page_pipeline import render_pages
from bookflow.paths import ProjectSettings, project_root
from bookflow.schemas import VISION_SCHEMA_VERSION, VisionPageResult
from bookflow.vision_pipeline import (
    VisionCallFailed,
    run_vision_page,
    vision_preflight,
)
from bookflow.vision_provider import ProviderResponse, ZhipuOpenAICompatibleProvider


SAMPLE = project_root() / "input" / "sample_11_pages.pdf"
FULL = project_root() / "input" / "The big game of central and western China (1913).pdf"
PROMPT = project_root() / "prompts" / "vision_transcription_v1.md"
KEY_ENV = "BOOKFLOW_TEST_ZAI_API_KEY"


def _settings(root: Path) -> ProjectSettings:
    return ProjectSettings.model_validate(
        {
            "source_pdf": str(FULL),
            "sample_pdf": str(SAMPLE),
            "output_directory": str(root / "output"),
            "cache_directory": str(root / "cache"),
            "render_dpi": 72,
            "page_image_directory": str(root / "pages"),
            "manifest_directory": str(root / "manifests"),
            "mock_vision_directory": str(root / "mock"),
            "continuity_directory": str(root / "continuity"),
            "log_directory": str(root / "logs"),
            "temporary_directory": str(root / "tmp"),
            "vision_provider": "configured-zhipu",
            "vision_base_url": "https://configured.example.invalid/v4",
            "vision_model": "configured-glm-vision",
            "vision_api_key_env": KEY_ENV,
            "vision_compatible_api_key_envs": [],
            "vision_prompt_path": str(PROMPT),
            "vision_raw_directory": str(root / "vision_raw"),
            "vision_normalized_directory": str(root / "vision_normalized"),
            "vision_request_directory": str(root / "vision_requests"),
            "vision_usage_directory": str(root / "vision_usage"),
            "vision_cache_directory": str(root / "vision_cache"),
            "vision_maximum_real_calls": 1,
            "vision_automatic_retry": False,
            "vision_api_enabled": False,
            "translation_enabled": False,
            "automatic_phase_advance": False,
            "translation_provider": "disabled-translation",
            "translation_base_url": "https://translation.example.invalid/v1",
            "translation_model": "disabled-model",
            "maximum_cash_cost_cny": 2.0,
            "default_page_range": [1, 11],
            "sample_page_range": [1, 11],
            "dry_run": True,
        }
    )


def _prepare(root: Path) -> ProjectSettings:
    settings = _settings(root)
    render_pages(SAMPLE, settings, pages="1", dpi=72, root=root)
    return settings


def _payload(preflight, **changes) -> dict:
    payload = {
        "schema_version": VISION_SCHEMA_VERSION,
        "document_id": preflight.document_id,
        "pdf_page": preflight.pdf_page,
        "provider": preflight.provider,
        "model": preflight.model,
        "page_type": "body_page",
        "printed_page": "12",
        "title": None,
        "running_header": "A RUNNING HEADER",
        "footer": None,
        "page_number_text": "12",
        "blocks": [
            {
                "block_id": "p0001-b001",
                "block_type": "body",
                "order": 1,
                "text": "An uncer[?]tain visible line—1907.",
                "bounding_box": None,
                "confidence": None,
                "uncertain": True,
                "notes": "One character is visually uncertain.",
            }
        ],
        "continuation_from_previous": True,
        "continuation_to_next": True,
        "boundary_notes": "The first and last visible lines are incomplete; no missing text was supplied.",
        "uncertain_characters": ["[?]"],
        "warnings": [],
        "status": "technical_validation_only",
        "translation_ready": False,
    }
    payload.update(changes)
    return payload


class FakeProvider:
    def __init__(self, response: ProviderResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def transcribe_one_page(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def _factory(instance: FakeProvider, captured: dict):
    def create(**kwargs):
        captured.update(kwargs)
        return instance

    return create


def _valid_fake(preflight) -> FakeProvider:
    payload = _payload(preflight)
    return FakeProvider(
        ProviderResponse(
            raw_response={
                "id": "mock-request-id",
                "model": preflight.model,
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 101, "completion_tokens": 202, "total_tokens": 303},
            },
            content=json.dumps(payload),
            request_id="mock-request-id",
            usage={"prompt_tokens": 101, "completion_tokens": 202, "total_tokens": 303},
            response_model=preflight.model,
        )
    )


def test_missing_key_preflight_and_call_are_offline(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    report = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    assert report.api_key_set is False
    assert report.api_called is False
    assert report.expected_real_calls == 1
    assert not report.ready_for_real_call
    with pytest.raises(RuntimeError, match="API key"):
        run_vision_page(
            SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
            provider_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("provider created")),
        )


def test_doctor_and_preflight_never_display_secret(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    secret = "TEST-ONLY-DO-NOT-STORE"
    monkeypatch.setenv(KEY_ENV, secret)
    report = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    serialized = report.model_dump_json()
    assert report.api_key_set is True
    assert secret not in serialized
    doctor_text = format_doctor_report(run_doctor(root=project_root()))
    assert secret not in doctor_text


def test_allow_api_and_confirmation_are_both_required(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    forbidden = lambda **kwargs: (_ for _ in ()).throw(AssertionError("provider created"))
    dry = run_vision_page(SAMPLE, [1], settings, root=tmp_path, provider_factory=forbidden)
    assert dry.status == "dry_run_api_not_allowed"
    assert dry.api_called is False
    with pytest.raises(PermissionError, match="confirm-one-call"):
        run_vision_page(SAMPLE, [1], settings, allow_api=True, root=tmp_path, provider_factory=forbidden)


def test_more_than_one_page_is_rejected_before_provider(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        run_vision_page(SAMPLE, [1, 2], settings, root=tmp_path)


def test_full_pdf_is_rejected_before_opening(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(page_module.fitz, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    with pytest.raises(PermissionError, match="full PDF"):
        vision_preflight(FULL, 1, settings, root=tmp_path)


def test_provider_uses_configured_model_base_url_base64_and_no_sdk_retry(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    preflight = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    fake = _valid_fake(preflight)
    captured: dict = {}
    result = run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=_factory(fake, captured),
    )
    assert captured["base_url"] == settings.vision_base_url
    assert fake.calls[0]["model"] == settings.vision_model
    assert fake.calls[0]["image_data_url"].startswith("data:image/png;base64,")
    assert fake.calls[0]["max_output_tokens"] == 8000
    assert settings.vision_automatic_retry is False
    assert result.retries == 0


def test_openai_compatible_provider_builds_expected_mock_http_request():
    captured: dict = {}

    class Completions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return {
                "id": "mock-http-id",
                "model": "configured-model",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"total_tokens": 1},
            }

    class Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": Completions()})()

    def client_factory(**kwargs):
        captured["client"] = kwargs
        return Client()

    provider = ZhipuOpenAICompatibleProvider(
        api_key="TEST-ONLY", base_url="https://configured.invalid/v4", timeout_seconds=7,
        client_factory=client_factory,
    )
    response = provider.transcribe_one_page(
        model="configured-model", prompt="prompt", context_message="context",
        image_data_url="data:image/png;base64,AA==", max_output_tokens=8000,
        temperature=0, do_sample=False, thinking_mode="disabled",
        response_format_json_object=False,
    )
    assert captured["client"]["base_url"] == "https://configured.invalid/v4"
    assert captured["client"]["max_retries"] == 0
    assert captured["request"]["messages"][1]["content"][1]["type"] == "image_url"
    assert "temperature" not in captured["request"]
    assert captured["request"]["extra_body"]["do_sample"] is False
    assert captured["request"]["extra_body"]["thinking"] == {"type": "disabled"}
    assert "response_format" not in captured["request"]
    assert response.request_id == "mock-http-id"


def test_secret_and_base64_are_not_written_to_records(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    secret = "TEST-ONLY-SENSITIVE-VALUE"
    monkeypatch.setenv(KEY_ENV, secret)
    preflight = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    fake = _valid_fake(preflight)
    run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=_factory(fake, {}),
    )
    recorded = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in tmp_path.rglob("*.json")
    )
    assert secret not in recorded
    assert "data:image/png;base64," not in recorded
    assert "DEEPSEEK_API_KEY" not in recorded


def test_same_fingerprint_uses_cache_and_persistent_limit_blocks_new_request(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    preflight = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    fake = _valid_fake(preflight)
    first = run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=_factory(fake, {}),
    )
    second = run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("second provider")),
    )
    assert first.api_called is True
    assert second.api_called is False and second.cache_hit is True
    assert len(fake.calls) == 1
    settings.vision_temperature = 0.1
    blocked = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    assert blocked.cache_hit is False
    assert blocked.remaining_real_calls == 0
    with pytest.raises(RuntimeError, match="limit"):
        run_vision_page(
            SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
            provider_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("second call")),
        )


@pytest.mark.parametrize(
    ("error", "expected_type", "expected_status"),
    [
        (type("AuthenticationFailure", (RuntimeError,), {"status_code": 401})("denied"), "AuthenticationFailure", 401),
        (TimeoutError("timed out"), "TimeoutError", None),
        (type("RateLimitFailure", (RuntimeError,), {"status_code": 429})("limited"), "RateLimitFailure", 429),
    ],
)
def test_provider_failures_are_saved_once_and_never_retried(
    tmp_path, monkeypatch, error, expected_type, expected_status
):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    fake = FakeProvider(error=error)
    with pytest.raises(VisionCallFailed) as caught:
        run_vision_page(
            SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
            provider_factory=_factory(fake, {}),
        )
    assert caught.value.error_type == expected_type
    assert caught.value.http_status == expected_status
    assert len(fake.calls) == 1
    saved = load_json(caught.value.raw_response_path)
    assert saved["automatic_retry"] is False
    assert saved["retries"] == 0


def test_invalid_json_is_not_retried_and_is_marked_needs_review(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    fake = FakeProvider(
        ProviderResponse(
            raw_response={"id": "bad-json", "choices": [{"message": {"content": "not json"}}]},
            content="not json", request_id="bad-json", usage=None, response_model=settings.vision_model,
        )
    )
    result = run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=_factory(fake, {}),
    )
    normalized = VisionPageResult.model_validate(load_json(result.normalized_output_path))
    validation = load_json(result.validation_path)
    assert len(fake.calls) == 1
    assert normalized.status == "needs_review"
    assert normalized.blocks == []
    assert validation["valid"] is False


def test_raw_and_normalized_are_separate_and_visual_fields_are_preserved(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    preflight = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    fake = _valid_fake(preflight)
    result = run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=_factory(fake, {}),
    )
    assert Path(result.raw_response_path) != Path(result.normalized_output_path)
    normalized = VisionPageResult.model_validate(load_json(result.normalized_output_path))
    assert normalized.uncertain_characters == ["[?]"]
    assert normalized.blocks[0].text == "An uncer[?]tain visible line—1907."
    assert normalized.continuation_from_previous is True
    assert normalized.continuation_to_next is True
    assert "no missing text" in normalized.boundary_notes
    assert normalized.authoritative is False
    assert normalized.translation_ready is False


def test_unknown_schema_field_is_not_silently_removed(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    preflight = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    payload = _payload(preflight, unexpected_field="must fail")
    fake = FakeProvider(
        ProviderResponse(
            raw_response={"id": "extra", "choices": [{"message": {"content": json.dumps(payload)}}]},
            content=json.dumps(payload), request_id="extra", usage=None, response_model=settings.vision_model,
        )
    )
    result = run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=_factory(fake, {}),
    )
    assert load_json(result.normalized_output_path)["status"] == "needs_review"
    validation = load_json(result.validation_path)
    assert validation["valid"] is False
    assert validation["unknown_fields_silently_removed"] is False


def test_phase2a_creates_no_logical_blocks_translation_or_full_pdf_outputs(tmp_path, monkeypatch):
    settings = _prepare(tmp_path)
    monkeypatch.setenv(KEY_ENV, "TEST-ONLY")
    preflight = vision_preflight(SAMPLE, 1, settings, root=tmp_path)
    fake = _valid_fake(preflight)
    result = run_vision_page(
        SAMPLE, [1], settings, allow_api=True, confirm_one_call=True, root=tmp_path,
        provider_factory=_factory(fake, {}),
    )
    metadata = load_json(result.request_metadata_path)
    assert metadata["translation_calls"] == 0
    assert not (tmp_path / "logical_blocks").exists()
    assert str(FULL) not in "\n".join(str(path) for path in tmp_path.rglob("*"))
