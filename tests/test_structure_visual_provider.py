"""Tests for Phase 1C-B mocked OpenAI-compatible visual provider."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bookflow.structure_visual_provider import (
    OpenAICompatibleStructureVisualProvider,
    build_mock_response_dict,
)
from bookflow.structure_visual_schemas import (
    VisualProviderRequest,
)

ROOT = Path(__file__).resolve().parents[1]


def _make_provider_request(page: int = 1) -> VisualProviderRequest:
    return VisualProviderRequest(
        request_id=f"visreq_p{page:04d}",
        physical_page=page,
        source_page_asset_ref="data/fullbook/pages/test/page_0001.png",
        prompt="Classify this page structure. Output JSON only.",
        context_json=json.dumps({"physical_page": page}),
        request_fingerprint="fp_test",
    )


def _mock_transport_returning(response_dict: dict):
    """Create a mock transport that returns the given response dict."""
    def transport(payload):
        return json.dumps(response_dict)
    return transport


class TestProviderParsing:
    def test_mock_response_parses_to_valid_response(self):
        resp_dict = build_mock_response_dict(
            physical_page=1,
            primary_role="blank",
            blank_kind="unknown_blank",
            content_features=[],
            original_book_content=False,
            contains_prose=False,
            safe_to_exclude=True,
            field_evidence=[
                {
                    "field_name": "primary_role",
                    "observed": "page appears empty",
                    "basis": "visual",
                    "confidence": 0.85,
                }
            ],
            confidence={"primary_role": 0.85},
        )
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(resp_dict),
        )
        result = provider.classify_page(_make_provider_request(1))
        assert result.response.primary_role.value == "blank"
        assert result.response.blank_kind.value == "unknown_blank"
        assert result.error is None

    def test_invalid_json_raises_clear_error(self):
        def bad_transport(payload):
            return "this is not json {{{"
        provider = OpenAICompatibleStructureVisualProvider(transport=bad_transport)
        with pytest.raises((json.JSONDecodeError, ValueError)):
            provider.classify_page(_make_provider_request(1))

    def test_schema_validation_error_raises_clear_error(self):
        bad_dict = build_mock_response_dict(physical_page=1)
        # Use a non-blank role with blank_kind set to trigger schema error
        bad_dict["primary_role"] = "chapter_body"
        bad_dict["blank_kind"] = "intentional_blank"
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(bad_dict),
        )
        with pytest.raises(ValueError, match="schema validation"):
            provider.classify_page(_make_provider_request(1))

    def test_page_mismatch_rejected(self):
        resp_dict = build_mock_response_dict(physical_page=99)
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(resp_dict),
        )
        with pytest.raises(ValueError, match="physical_page"):
            provider.classify_page(_make_provider_request(1))


class TestProviderPayload:
    def test_mock_transport_receives_expected_payload(self):
        received_payloads = []

        def capturing_transport(payload):
            received_payloads.append(payload)
            return json.dumps(build_mock_response_dict(physical_page=1))

        provider = OpenAICompatibleStructureVisualProvider(
            transport=capturing_transport,
        )
        provider.classify_page(_make_provider_request(1))

        assert len(received_payloads) == 1
        payload = received_payloads[0]
        assert payload["model"] == "structure-visual-mock"
        assert "messages" in payload
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        # Image reference should be asset:// not base64
        user_content = payload["messages"][1]["content"]
        assert isinstance(user_content, list)
        image_part = [c for c in user_content if c.get("type") == "image_url"]
        assert len(image_part) == 1
        assert image_part[0]["image_url"]["url"].startswith("asset://")
        assert "base64" not in image_part[0]["image_url"]["url"].lower()

    def test_transport_can_return_dict(self):
        resp_dict = build_mock_response_dict(physical_page=1)
        def dict_transport(payload):
            return resp_dict
        provider = OpenAICompatibleStructureVisualProvider(transport=dict_transport)
        result = provider.classify_page(_make_provider_request(1))
        assert result.response.physical_page == 1


class TestNetworkBlocking:
    def test_no_transport_raises_not_network(self):
        provider = OpenAICompatibleStructureVisualProvider()
        with pytest.raises(RuntimeError, match="No transport"):
            provider.classify_page(_make_provider_request(1))

    def test_allow_network_true_raises_not_implemented(self):
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(build_mock_response_dict(1)),
        )
        with pytest.raises(NotImplementedError):
            provider.classify_page(_make_provider_request(1), allow_network=True)

    def test_constructor_allow_network_true_raises_not_implemented(self):
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(build_mock_response_dict(1)),
            allow_network=True,
        )
        with pytest.raises(NotImplementedError):
            provider.classify_page(_make_provider_request(1))

    def test_no_real_http_request(self, monkeypatch):
        """Ensure no socket connection is attempted."""
        original_socket = socket.socket

        def blocking_socket(*args, **kwargs):
            raise RuntimeError("Network access blocked in test")

        monkeypatch.setattr(socket, "socket", blocking_socket)

        # Also block requests/httpx if present
        try:
            import requests
            monkeypatch.setattr(requests, "request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
            monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
            monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
        except ImportError:
            pass

        try:
            import httpx
            monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
            monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
        except ImportError:
            pass

        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(build_mock_response_dict(1)),
        )
        # Should succeed with mock transport despite network being blocked
        result = provider.classify_page(_make_provider_request(1))
        assert result.response.physical_page == 1

        # Restore socket for cleanup
        monkeypatch.setattr(socket, "socket", original_socket)

    def test_automatic_retry_is_false(self):
        provider = OpenAICompatibleStructureVisualProvider()
        assert provider.automatic_retry is False


class TestApiKeySafety:
    def test_no_env_api_key_read(self, monkeypatch):
        """Provider must not read API/KEY environment variables."""
        accessed = []
        original_getenv = __import__("os").getenv

        def fake_getenv(key, default=None):
            if "KEY" in key.upper() or "API" in key.upper():
                accessed.append(key)
            return original_getenv(key, default)

        monkeypatch.setattr("os.getenv", fake_getenv)
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(build_mock_response_dict(1)),
        )
        provider.classify_page(_make_provider_request(1))
        assert accessed == []


class TestForbiddenFields:
    def test_response_with_join_operation_rejected(self):
        d = build_mock_response_dict(physical_page=1)
        d["join_operation"] = "insert_space"
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(d),
        )
        with pytest.raises(ValueError, match="forbidden field"):
            provider.classify_page(_make_provider_request(1))

    def test_response_with_structural_break_rejected(self):
        d = build_mock_response_dict(physical_page=1)
        d["structural_break"] = "chapter_break"
        provider = OpenAICompatibleStructureVisualProvider(
            transport=_mock_transport_returning(d),
        )
        with pytest.raises(ValueError, match="forbidden field"):
            provider.classify_page(_make_provider_request(1))
