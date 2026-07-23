"""Mocked OpenAI-compatible adapter for Phase 1C-B visual structure classification.

This module provides a provider that constructs OpenAI-compatible chat
completion payloads and parses responses into ``VisualPageClassificationResponse``
objects.  It NEVER touches the network, reads no API keys, and relies on
dependency-injected transports (Mock) for all testing.

Real network calls belong to Phase 1C-C and are blocked with
``NotImplementedError`` even if ``allow_network=True`` is passed.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

from .structure_visual_schemas import (
    FORBIDDEN_RESPONSE_FIELDS,
    VisualPageClassificationResponse,
    VisualProviderRequest,
    VisualProviderResult,
)

# ---------------------------------------------------------------------------
# Transport protocol
# ---------------------------------------------------------------------------


class MockTransport(Protocol):
    """A callable that receives an OpenAI-compatible payload and returns content."""

    def __call__(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenAICompatibleStructureVisualProvider:
    """OpenAI-compatible adapter for page structure classification.

    Uses dependency injection for the transport.  Without a transport, the
    provider raises immediately rather than attempting network access.
    """

    def __init__(
        self,
        *,
        model: str = "structure-visual-mock",
        transport: MockTransport | None = None,
        allow_network: bool = False,
    ) -> None:
        self._model = model
        self._transport = transport
        self._allow_network = allow_network
        self._automatic_retry = False

    @property
    def automatic_retry(self) -> bool:
        return self._automatic_retry

    def _build_payload(self, request: VisualProviderRequest) -> dict[str, Any]:
        """Construct an OpenAI-compatible chat completion payload."""
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": request.context_json},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"asset://{request.source_page_asset_ref}",
                    "format": "png",
                },
            },
        ]
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
            "extra_body": {
                "do_sample": False,
                "thinking": {"type": "normal"},
            },
        }

    def classify_page(
        self,
        request: VisualProviderRequest,
        *,
        allow_network: bool = False,
    ) -> VisualProviderResult:
        """Classify a single page using the injected transport.

        Raises ``NotImplementedError`` if real network access is requested
        (Phase 1C-C scope), or ``RuntimeError`` if no transport is available.
        """
        # Block all network access in Phase 1C-B.
        if allow_network or self._allow_network:
            raise NotImplementedError(
                "Real network calls belong to Phase 1C-C; "
                "Phase 1C-B only supports Mock transports"
            )

        if self._transport is None:
            raise RuntimeError(
                "No transport provided; cannot classify without a Mock transport"
            )

        payload = self._build_payload(request)
        raw_result = self._transport(payload)

        # Normalize to string for storage
        if isinstance(raw_result, dict):
            raw_content = json.dumps(raw_result, ensure_ascii=False)
            response_dict = raw_result
        elif isinstance(raw_result, str):
            raw_content = raw_result
            response_dict = json.loads(raw_result)
        else:
            raise TypeError(
                f"Mock transport must return str or dict, got {type(raw_result).__name__}"
            )

        # Check for forbidden fields
        for field in FORBIDDEN_RESPONSE_FIELDS:
            if field in response_dict:
                raise ValueError(
                    f"Response contains forbidden field '{field}'; "
                    "visual classification must not modify boundary data"
                )

        # Parse into schema
        try:
            response = VisualPageClassificationResponse.model_validate(response_dict)
        except Exception as exc:
            raise ValueError(
                f"Response schema validation failed for p{request.physical_page}: {exc}"
            ) from exc

        # Verify page match
        if response.physical_page != request.physical_page:
            raise ValueError(
                f"Response physical_page ({response.physical_page}) does not match "
                f"request physical_page ({request.physical_page})"
            )

        raw_ref = f"mock://{request.request_id}"

        return VisualProviderResult(
            request_id=request.request_id,
            physical_page=request.physical_page,
            response=response,
            raw_content=raw_content,
            raw_response_ref=raw_ref,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            error=None,
        )


# ---------------------------------------------------------------------------
# Helper: build a mock response dict for testing
# ---------------------------------------------------------------------------


def build_mock_response_dict(
    physical_page: int,
    *,
    primary_role: str = "unknown",
    blank_kind: str | None = None,
    content_features: list[str] | None = None,
    original_book_content: bool = False,
    contains_prose: bool = False,
    safe_to_exclude: bool = True,
    requires_region_analysis: bool = False,
    printed_page_label: str | None = None,
    printed_page_number: int | None = None,
    numbering_scheme: str = "unknown",
    page_side: str = "unknown",
    field_evidence: list[dict] | None = None,
    confidence: dict[str, float] | None = None,
    warnings: list[dict] | None = None,
    reviewer_notes: str = "",
) -> dict[str, Any]:
    """Build a valid mock response dictionary for testing."""
    if content_features is None:
        content_features = []
    if field_evidence is None:
        field_evidence = []
    if confidence is None:
        confidence = {"primary_role": 0.5}
    if warnings is None:
        warnings = []

    return {
        "schema_version": "1.0",
        "physical_page": physical_page,
        "primary_role": primary_role,
        "blank_kind": blank_kind,
        "content_features": content_features,
        "artifact_overlays": [],
        "original_book_content": original_book_content,
        "contains_prose": contains_prose,
        "safe_to_exclude_from_prose_flow": safe_to_exclude,
        "requires_region_analysis": requires_region_analysis,
        "printed_page_label": printed_page_label,
        "printed_page_number": printed_page_number,
        "numbering_scheme": numbering_scheme,
        "page_side": page_side,
        "field_evidence": field_evidence,
        "confidence_by_field": confidence,
        "warnings": warnings,
        "reviewer_notes": reviewer_notes,
    }
