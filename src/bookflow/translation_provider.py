"""OpenAI-compatible DeepSeek provider with SDK retries disabled."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported provider response type: {type(value).__name__}")


@dataclass(frozen=True)
class TranslationProviderResponse:
    raw_response: dict[str, Any]
    content: str
    request_id: str | None
    usage: dict[str, Any] | None
    response_model: str | None


class DeepSeekOpenAICompatibleProvider:
    """Use only configured DeepSeek-compatible endpoints; never retry in the SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        client_factory: Callable[..., Any] = OpenAI,
    ) -> None:
        self._client = client_factory(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def list_model_ids(self) -> set[str]:
        raw = _as_dict(self._client.models.list())
        data = raw.get("data") or []
        return {
            str(item["id"])
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }

    def translate_one(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        max_output_tokens: int,
        temperature: float,
        thinking_mode: str,
    ) -> TranslationProviderResponse:
        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            max_tokens=max_output_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": thinking_mode}},
        )
        raw = _as_dict(response)
        choices = raw.get("choices") or []
        content = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message.get("content"), str):
                content = message["content"]
        usage_value = raw.get("usage")
        usage = _as_dict(usage_value) if usage_value is not None else None
        request_id = raw.get("id") or getattr(response, "_request_id", None)
        return TranslationProviderResponse(
            raw_response=raw,
            content=content,
            request_id=str(request_id) if request_id else None,
            usage=usage,
            response_model=str(raw.get("model")) if raw.get("model") else None,
        )
