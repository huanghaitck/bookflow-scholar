"""Single-request OpenAI-compatible visual provider for Phase 2A."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI


@dataclass(frozen=True)
class ProviderResponse:
    raw_response: dict[str, Any]
    content: str
    request_id: str | None
    usage: dict[str, Any] | None
    response_model: str | None
    http_status: int | None = None


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported provider response type: {type(value).__name__}")


class ZhipuOpenAICompatibleProvider:
    """Call one configured visual request with SDK retries disabled."""

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

    def transcribe_one_page(
        self,
        *,
        model: str,
        prompt: str,
        context_message: str,
        image_data_url: str,
        max_output_tokens: int,
        temperature: float,
        do_sample: bool,
        thinking_mode: str,
        response_format_json_object: bool,
    ) -> ProviderResponse:
        return self.transcribe_images(
            model=model,
            prompt=prompt,
            context_message=context_message,
            image_data_urls=[image_data_url],
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            do_sample=do_sample,
            thinking_mode=thinking_mode,
            response_format_json_object=response_format_json_object,
        )

    def transcribe_images(
        self,
        *,
        model: str,
        prompt: str,
        context_message: str,
        image_data_urls: list[str],
        max_output_tokens: int,
        temperature: float,
        do_sample: bool,
        thinking_mode: str,
        response_format_json_object: bool,
        response_json_schema: dict[str, Any] | None = None,
        response_schema_name: str = "bookflow_vision_response",
        return_raw_on_content_error: bool = False,
    ) -> ProviderResponse:
        if not image_data_urls:
            raise ValueError("At least one image is required")
        user_content: list[dict[str, Any]] = [{"type": "text", "text": context_message}]
        user_content.extend(
            {"type": "image_url", "image_url": {"url": value}}
            for value in image_data_urls
        )
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_output_tokens,
        }
        extra_body: dict[str, Any] = {
            "do_sample": do_sample,
            "thinking": {"type": thinking_mode},
        }
        if do_sample:
            request["temperature"] = temperature
        request["extra_body"] = extra_body
        if response_json_schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema_name,
                    "strict": True,
                    "schema": response_json_schema,
                },
            }
        elif response_format_json_object:
            request["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(**request)
        raw = _as_dict(response)
        choices = raw.get("choices") or []
        if not choices and not return_raw_on_content_error:
            raise ValueError("Provider response contains no choices")
        message = choices[0].get("message") or {} if choices else {}
        content = message.get("content")
        if (not isinstance(content, str) or not content.strip()) and not return_raw_on_content_error:
            raise ValueError("Provider response contains no text content")
        if not isinstance(content, str):
            content = ""
        usage_value = raw.get("usage")
        usage = _as_dict(usage_value) if usage_value is not None else None
        request_id = raw.get("id") or getattr(response, "_request_id", None)
        response_handle = getattr(response, "_response", None)
        http_status = getattr(response_handle, "status_code", None)
        return ProviderResponse(
            raw_response=raw,
            content=content,
            request_id=str(request_id) if request_id else None,
            usage=usage,
            response_model=str(raw.get("model")) if raw.get("model") else None,
            http_status=int(http_status) if isinstance(http_status, int) else None,
        )
