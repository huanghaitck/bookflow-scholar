from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderContractError(ValueError):
    reason: str
    response_type: str | None = None
    response_length: int | None = None
    response_sha256: str | None = None
    finish_reason: str | None = None
    json_error: str | None = None
    schema_path: str | None = None
    http_status: int | None = None

    def __str__(self):
        return self.reason


def _content_metadata(value: Any) -> dict:
    if isinstance(value, bytes): raw = value
    elif isinstance(value, str): raw = value.encode("utf-8", errors="replace")
    else:
        try: raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        except Exception: raw = type(value).__name__.encode()
    return {"response_type": type(value).__name__, "response_length": len(raw), "response_sha256": hashlib.sha256(raw).hexdigest()}


def _strip_json_fence(content: str) -> str:
    value = content.lstrip("\ufeff").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"} or lines[-1].strip() != "```":
            raise ProviderContractError("invalid JSON code fence", **_content_metadata(content), schema_path="content")
        value = "\n".join(lines[1:-1]).strip()
    return value


def _single_result(response: Any, unit: dict) -> dict:
    """Map one translate_one result to one Bookflow result; request IDs are authoritative."""
    from ..translation_provider import TranslationProviderResponse

    model_content = isinstance(response, TranslationProviderResponse)
    finish_reason = None
    if model_content:
        choices = response.raw_response.get("choices") or []
        if choices and isinstance(choices[0], dict): finish_reason = choices[0].get("finish_reason")
        response = response.content

    if isinstance(response, str):
        text = _strip_json_fence(response)
        if not text: raise ProviderContractError("empty model content", **_content_metadata(response), finish_reason=finish_reason, schema_path="content")
        if model_content:
            try: response = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProviderContractError("model content is not complete JSON", **_content_metadata(text), finish_reason=finish_reason, json_error=f"{exc.msg} at line {exc.lineno} column {exc.colno}", schema_path="content") from exc
        else:
            response = {"translated_text": text}

    if not isinstance(response, dict):
        raise ProviderContractError("unsupported translate_one result", **_content_metadata(response), finish_reason=finish_reason, schema_path="$" )
    if "translations" in response:
        values = response["translations"]
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise ProviderContractError("single call must contain exactly one translation", **_content_metadata(response), finish_reason=finish_reason, schema_path="$.translations")
        response = values[0]
    elif "content" in response and "translated_text" not in response:
        return _single_result(str(response["content"]), unit)

    returned_id = response.get("translation_unit_id")
    returned_object = response.get("source_object_id")
    if returned_id not in {None, unit["translation_unit_id"]}:
        raise ProviderContractError("translation_unit_id mismatch", **_content_metadata(response), finish_reason=finish_reason, schema_path="$.translation_unit_id")
    if returned_object not in {None, unit["source_object_id"]}:
        raise ProviderContractError("source_object_id mismatch", **_content_metadata(response), finish_reason=finish_reason, schema_path="$.source_object_id")
    text = response.get("translated_text")
    if not isinstance(text, str) or not text.strip():
        raise ProviderContractError("missing or empty translated_text", **_content_metadata(response), finish_reason=finish_reason, schema_path="$.translated_text")
    result = {
        "translation_unit_id": unit["translation_unit_id"],
        "source_object_id": unit["source_object_id"],
        "translated_text": text.strip(),
        "status": response.get("status", "translated"),
    }
    for key, value in response.items():
        if key.startswith("placeholder") and key not in result: result[key] = value
    return result


class OpenAICompatibleTranslationProvider:
    def __init__(self, config: dict, transport=None):
        self.config, self.transport = config, transport
    def health_check(self):
        if self.transport is None: return {"ok": False, "provider": "openai_compatible"}
        check = getattr(self.transport, "health_check", None)
        return check() if check else {"ok": True, "provider": "openai_compatible"}
    def estimate_request(self, units): return {"units": len(units), "estimated_tokens": sum(len(u["source_text"]) for u in units) // 4 + 1}
    def translate_batch(self, units):
        if self.transport is None: raise RuntimeError("network transport disabled")
        return self.normalize_response(self.transport({"model": self.config["model"], "units": units}))
    def normalize_response(self, response):
        values = response.get("translations")
        if not isinstance(values, list) or any("translation_unit_id" not in x or "translated_text" not in x for x in values):
            raise ValueError("malformed provider response")
        return values


def deepseek_transport(config: dict, client=None):
    """Adapt the existing DeepSeek client to the batch provider contract."""
    import os
    from ..translation_provider import DeepSeekOpenAICompatibleProvider

    api_key = os.getenv(config["api_key_env"], "")
    client = client or DeepSeekOpenAICompatibleProvider(api_key=api_key, base_url=config["base_url"], timeout_seconds=float(config.get("timeout_seconds", 60)))

    def call(payload):
        translations = []
        for unit in payload["units"]:
            response = client.translate_one(
                model=payload["model"],
                system_prompt=(f"Translate this unit faithfully from {unit.get('source_language', 'the source language')} "
                               f"to {unit.get('target_language', 'the target language')}. Return a JSON object containing "
                               "translated_text. Preserve every placeholder and original proper name spelling where required."),
                user_payload={"source_text": unit["source_text"], "placeholders": unit.get("placeholders", [])},
                max_output_tokens=int(config.get("max_output_tokens", 8192)),
                temperature=float(config.get("temperature", 0)),
                thinking_mode=str(config.get("thinking_mode", "disabled")),
            )
            translations.append(_single_result(response, unit))
        return {"translations": translations}

    call.health_check = lambda: {  # type: ignore[attr-defined]
        "ok": config["model"] in client.list_model_ids(),
        "provider": "openai_compatible",
    }
    return call


class OpenAICompatibleVisionProvider:
    def __init__(self, config: dict, transport=None): self.config, self.transport = config, transport
    def health_check(self): return {"ok": self.transport is not None, "provider": "openai_compatible"}
    def analyze_page(self, page):
        if self.transport is None: raise RuntimeError("network transport disabled")
        return self.normalize_response(self.transport({"model": self.config["model"], "page": page}))
    def normalize_response(self, response):
        if not isinstance(response.get("observations"), list): raise ValueError("malformed provider response")
        return response
