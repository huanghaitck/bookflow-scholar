"""Configurable provider registry shared by CLI, UI, text, and vision workflows."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .credential_store import credential_status, resolve_secret


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


_PROVIDER_ATTEMPT_LOCK = threading.RLock()


def _append_provider_attempt(path: Path | None, item: dict[str, Any]) -> None:
    if path is None:
        return
    required = {
        "attempt_id", "project_id", "job_id", "stage", "provider_role", "provider", "model",
        "page_or_segment_id", "purpose", "start", "end", "latency", "status", "retry_number",
        "usage", "request_hash", "response_hash", "error_code",
    }
    missing = required - set(item)
    if missing:
        raise ValueError(f"provider attempt is missing fields: {sorted(missing)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _PROVIDER_ATTEMPT_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n")


class ProviderSchemaError(ValueError):
    """A successful provider response that does not satisfy the translation contract."""


NOTE_PLACEHOLDER_PATTERN = re.compile(r"\{\{NOTE_REF:[^:}]+:[^}]+\}\}")
PROTECTED_LITERAL_PATTERN = re.compile(
    r"""(?ix)
    (?:
        https?://[^\s<>{}\[\]"']+
        | www\.[^\s<>{}\[\]"']+
        | \b10\.\d{4,9}/[-._;()/:a-z0-9]+\b
        | \b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b
    )
    """
)


def _protected_literal_terms(value: str) -> tuple[str, ...]:
    """Return URL/DOI/email literals that must survive translation unchanged."""
    trailing = ".,;:!?)]}"
    terms: list[str] = []
    for match in PROTECTED_LITERAL_PATTERN.finditer(value):
        term = match.group(0).rstrip(trailing)
        if term and term not in terms:
            terms.append(term)
    return tuple(terms)


def _protected_term_markers(terms: tuple[str, ...]) -> dict[str, str]:
    return {f"{{{{PROTECTED_TERM:{index}}}}}": term for index, term in enumerate(terms)}


def _mask_protected_terms(value: str, markers: dict[str, str]) -> str:
    for marker, term in markers.items():
        value = re.sub(
            rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", marker, value,
        )
    return value


def _restore_protected_terms(value: str, markers: dict[str, str]) -> str:
    for marker, term in markers.items():
        value = value.replace(marker, term)
    return value


def _validate_preserved_placeholders(source_text: str, translated_text: str) -> None:
    source = NOTE_PLACEHOLDER_PATTERN.findall(source_text)
    target = NOTE_PLACEHOLDER_PATTERN.findall(translated_text)
    if source != target:
        raise ProviderSchemaError("provider_schema_error: translated_text changed note reference placeholders")


def _quality_text(value: str) -> str:
    value = NOTE_PLACEHOLDER_PATTERN.sub("", value)
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _validate_translation_fidelity(unit: dict[str, Any], translated_text: str) -> None:
    """Reject source echoes and clear source-script leakage before caching output."""
    source_language = str(unit["source_language"])
    target_language = str(unit["target_language"])
    if source_language == target_language:
        return
    source = _quality_text(str(unit["source_text"]))
    target = _quality_text(translated_text)
    protected_terms = tuple(str(term) for term in (unit.get("protected_terms") or ()))
    protected_identifier = any(source == _quality_text(term) for term in protected_terms)
    if protected_identifier and source == target:
        return
    if len(source) >= 4 and source == target and not protected_identifier:
        raise ProviderSchemaError("provider_schema_error: untranslated source echo")
    for term in protected_terms:
        if re.search(rf"(?<![A-Za-z]){re.escape(str(term))}(?![A-Za-z])", source, re.I):
            target_term = rf"(?<![A-Za-z]){re.escape(str(term))}(?:['’]s|s)?(?![A-Za-z])"
            if not re.search(target_term, translated_text):
                raise ProviderSchemaError("provider_schema_error: protected term changed")

    han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
    kana = re.compile(r"[\u3040-\u30ff]")
    latin_targets = {"en", "fr", "de", "es"}
    if target_language in latin_targets and source_language in {"zh-Hans", "ja"}:
        source_script_count = len(han.findall(source)) + len(kana.findall(source))
        leaked_script_count = len(han.findall(target)) + len(kana.findall(target))
        if source_script_count >= 8 and leaked_script_count >= max(6, source_script_count // 2):
            raise ProviderSchemaError("provider_schema_error: substantial source-script leakage")
    if target_language in {"zh-Hans", "ja"} and source_language in latin_targets:
        target_script_count = len(han.findall(target)) + len(kana.findall(target))
        source_letter_count = sum(character.isalpha() for character in source)
        if source_letter_count >= 20 and target_script_count == 0:
            raise ProviderSchemaError("provider_schema_error: target script is absent")


def _segmented_note_contract(source_text: str) -> dict[str, Any] | None:
    markers = NOTE_PLACEHOLDER_PATTERN.findall(source_text)
    if not markers:
        return None
    segments = NOTE_PLACEHOLDER_PATTERN.split(source_text)
    return {"version": "segmented-note-markers-v1", "markers": markers, "segments": segments}


def _structured_table_contract(source_text: str) -> dict[str, Any] | None:
    lines = source_text.splitlines(keepends=True)
    if sum(line.count("|") >= 2 for line in lines) < 2:
        return None
    template: list[dict[str, Any]] = []
    source_segments: list[dict[str, Any]] = []

    def literal(value: str) -> None:
        if value:
            template.append({"literal": value})

    def translatable(value: str) -> None:
        leading = value[:len(value) - len(value.lstrip())]
        trailing = value[len(value.rstrip()):]
        core = value.strip()
        literal(leading)
        if not core or re.fullmatch(r":?-{3,}:?", core) or not any(character.isalpha() for character in core):
            literal(core)
        else:
            segment_id = len(source_segments)
            source_segments.append({"id": segment_id, "text": core})
            template.append({"segment_id": segment_id})
        literal(trailing)

    for line in lines:
        newline = "\n" if line.endswith("\n") else ""
        content = line[:-1] if newline else line
        parts = content.split("|")
        for part_index, part in enumerate(parts):
            if part_index:
                literal("|")
            if part_index in {0, len(parts) - 1}:
                literal(part)
                continue
            cursor = 0
            for marker in NOTE_PLACEHOLDER_PATTERN.finditer(part):
                translatable(part[cursor:marker.start()])
                literal(marker.group(0))
                cursor = marker.end()
            translatable(part[cursor:])
        literal(newline)
    if not source_segments:
        return None
    return {
        "version": "structured-table-cells-v1",
        "source_segments": source_segments,
        "template": template,
    }


def _rebuild_segmented_translation(
    value: dict[str, Any], contract: dict[str, Any], unit: dict[str, Any],
) -> "TranslationResult":
    translated_segments = value.get("translated_segments")
    if not isinstance(translated_segments, list):
        raise ProviderSchemaError("provider_schema_error: translated_segments must be an array")
    by_id: dict[int, str] = {}
    for item in translated_segments:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int) or not isinstance(item.get("text"), str):
            raise ProviderSchemaError("provider_schema_error: each translated segment needs integer id and text")
        by_id[item["id"]] = item["text"].strip()
    required = {int(item["id"]) for item in contract["source_segments"]}
    if set(by_id) != required:
        raise ProviderSchemaError("provider_schema_error: translated segment ids do not match source segments")
    pieces = [
        str(item["literal"]) if "literal" in item else by_id[int(item["segment_id"])]
        for item in contract["template"]
    ]
    canonical = TranslationResult(
        translated_text="".join(pieces), source_language=unit["source_language"],
        target_language=unit["target_language"],
    )
    _validate_preserved_placeholders(unit["source_text"], canonical.translated_text)
    return canonical


def _protected_document_terms(units: list[dict[str, Any]]) -> tuple[str, ...]:
    """Find repeated table row identifiers without treating headings as names."""
    table_units = [
        unit for unit in units
        if unit.get("preserve_structure")
        or _structured_table_contract(str(unit["source_text"])) is not None
    ]
    candidates: set[str] = set()
    for unit in table_units:
        for line in str(unit["source_text"]).splitlines():
            for cell in line.strip().strip("|").split("|"):
                value = NOTE_PLACEHOLDER_PATTERN.sub("", cell).strip()
                if re.fullmatch(r"[A-Z][A-Za-z'’-]{2,}", value):
                    candidates.add(value)
    row_identifiers: set[str] = set()
    for unit in table_units:
        rows = [
            [NOTE_PLACEHOLDER_PATTERN.sub("", cell).strip() for cell in line.strip().strip("|").split("|")]
            for line in str(unit["source_text"]).splitlines()
            if line.count("|") >= 2
        ]
        separator_index = next((
            index for index, row in enumerate(rows)
            if row and all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)
        ), None)
        if separator_index is None:
            continue
        row_identifiers.update(row[0] for row in rows[separator_index + 1:] if row)
    candidates.intersection_update(row_identifiers)
    protected: list[str] = []
    for candidate in sorted(candidates):
        pattern = re.compile(rf"(?<![A-Za-z]){re.escape(candidate)}(?![A-Za-z])")
        if sum(bool(pattern.search(str(unit["source_text"]))) for unit in units) >= 2:
            protected.append(candidate)
    return tuple(protected)


def _workspace_protected_document_terms(
    workspace: Path, units: list[dict[str, Any]],
) -> tuple[str, ...]:
    document_units = units
    source = workspace / "data" / "translation_units.jsonl"
    if source.is_file():
        try:
            loaded = [
                json.loads(line) for line in source.read_text("utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            loaded = []
        if loaded:
            document_units = loaded
    return _protected_document_terms(document_units)


def _normalize_unit_translation(value: dict[str, Any], unit: dict[str, Any], *,
                                allow_text_alias: bool = False) -> "TranslationResult":
    table_contract = _structured_table_contract(unit["source_text"]) if unit.get("preserve_structure") else None
    if table_contract is not None:
        return _rebuild_segmented_translation(value, table_contract, unit)
    contract = _segmented_note_contract(unit["source_text"])
    if contract is None:
        return normalize_translation_result(
            value, source_language=unit["source_language"], target_language=unit["target_language"],
            allow_text_alias=allow_text_alias,
        )
    translated_segments = value.get("translated_segments")
    if not isinstance(translated_segments, list):
        raise ProviderSchemaError("provider_schema_error: translated_segments must be an array")
    by_id: dict[int, str] = {}
    for item in translated_segments:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int) or not isinstance(item.get("text"), str):
            raise ProviderSchemaError("provider_schema_error: each translated segment needs integer id and text")
        by_id[item["id"]] = item["text"].strip()
    required = {index for index, text in enumerate(contract["segments"]) if text.strip()}
    if set(by_id) != required:
        raise ProviderSchemaError("provider_schema_error: translated segment ids do not match source segments")
    pieces: list[str] = []
    for index, source_segment in enumerate(contract["segments"]):
        pieces.append(by_id.get(index, "") if source_segment.strip() else source_segment)
        if index < len(contract["markers"]):
            pieces.append(contract["markers"][index])
    canonical = TranslationResult(
        translated_text="".join(pieces), source_language=unit["source_language"],
        target_language=unit["target_language"],
    )
    _validate_preserved_placeholders(unit["source_text"], canonical.translated_text)
    return canonical


class TranslationResult(BaseModel):
    """The only translation result shape allowed beyond the provider boundary."""

    model_config = ConfigDict(extra="allow")

    translated_text: str = Field(min_length=1)
    source_language: str = Field(min_length=1)
    target_language: str = Field(min_length=1)
    terminology: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("translated_text", "source_language", "target_language")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


def normalize_translation_result(
    value: dict[str, Any],
    *,
    source_language: str,
    target_language: str,
    allow_text_alias: bool = False,
) -> TranslationResult:
    """Normalize documented provider aliases and validate the canonical object."""
    normalized = dict(value)
    if "translated_text" not in normalized and "translation_text" in normalized:
        normalized["translated_text"] = normalized.pop("translation_text")
    if allow_text_alias and "translated_text" not in normalized and "text" in normalized:
        normalized["translated_text"] = normalized.pop("text")
    normalized.setdefault("source_language", source_language)
    normalized.setdefault("target_language", target_language)
    try:
        return TranslationResult.model_validate(normalized)
    except ValidationError as exc:
        missing = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        detail = ", ".join(missing) or "translation result"
        raise ProviderSchemaError(f"provider_schema_error: invalid or missing field(s): {detail}") from exc


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    provider_type: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    api_key_alias: str | None = None
    timeout_seconds: float = 60.0
    max_retries: int = 2
    rate_limit: float | None = None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    extra: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        status = credential_status(alias=self.api_key_alias, env_name=self.api_key_env)
        value["credential_present"] = status["present"]
        value["credential_source"] = status["source"]
        return value


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text("utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name and name.replace("_", "").isalnum():
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def _expand(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


class ProviderRegistry:
    def __init__(self, profiles: dict[str, ProviderProfile], *, allow_real_api: bool = False,
                 active_text: str | None = None, active_vision: str | None = None) -> None:
        self.profiles = profiles
        self.allow_real_api = allow_real_api
        self.active_text = active_text
        self.active_vision = active_vision

    @classmethod
    def load(cls, path: Path, *, credentials_profile: Path | None = None) -> "ProviderRegistry":
        path = path.resolve()
        if credentials_profile is not None:
            _load_env_file(credentials_profile.resolve())
        else:
            _load_env_file(path.parent / ".env")
            _load_env_file(path.parent.parent / ".env")
        data = _expand(yaml.safe_load(path.read_text("utf-8")) or {})
        if data.get("allow_real_api") not in {True, False}:
            raise ValueError("allow_real_api must be boolean")
        profiles: dict[str, ProviderProfile] = {}
        for provider_id, raw in (data.get("providers") or {}).items():
            if not isinstance(raw, dict):
                raise ValueError(f"provider {provider_id!r} must be a mapping")
            if "api_key" in raw:
                raise ValueError("literal API keys are forbidden")
            known = {"provider_id", "provider_type", "type", "model", "base_url", "api_key_env",
                     "api_key_alias", "timeout_seconds", "max_retries", "rate_limit", "capabilities", "extra"}
            extra = dict(raw.get("extra") or {})
            extra.update({key: value for key, value in raw.items() if key not in known})
            provider_type = str(raw.get("provider_type") or raw.get("type") or "")
            model = str(raw.get("model") or "")
            if not provider_type or not model:
                raise ValueError(f"provider {provider_id!r} requires provider_type and model")
            capabilities = tuple(str(x) for x in (raw.get("capabilities") or
                                 (["text", "vision", "structure"] if provider_type == "mock" else [])))
            profiles[provider_id] = ProviderProfile(
                provider_id=provider_id, provider_type=provider_type, model=model,
                base_url=raw.get("base_url"), api_key_env=raw.get("api_key_env"),
                api_key_alias=raw.get("api_key_alias"), timeout_seconds=float(raw.get("timeout_seconds", 60)),
                max_retries=int(raw.get("max_retries", 2)),
                rate_limit=float(raw["rate_limit"]) if raw.get("rate_limit") is not None else None,
                capabilities=capabilities, extra=extra,
            )
        return cls(profiles, allow_real_api=bool(data["allow_real_api"]),
                   active_text=data.get("active_text_provider") or data.get("active_translation_provider"),
                   active_vision=data.get("active_vision_provider"))

    def get(self, provider_id: str | None, capability: str) -> ProviderProfile:
        selected = provider_id or (self.active_vision if capability in {"vision", "structure", "ocr"} else self.active_text)
        if not selected or selected not in self.profiles:
            raise ValueError(f"no configured provider for capability {capability!r}")
        profile = self.profiles[selected]
        if capability not in profile.capabilities:
            raise ValueError(f"provider {selected!r} does not declare capability {capability!r}")
        return profile

    def validate(self) -> dict[str, Any]:
        records = []
        for profile in self.profiles.values():
            errors = []
            if profile.provider_type != "mock" and not profile.base_url:
                errors.append("base_url_missing")
            if profile.provider_type != "mock" and not profile.api_key_env:
                errors.append("api_key_env_missing")
            records.append({**profile.public_dict(), "valid": not errors, "errors": errors})
        return {"valid": all(item["valid"] for item in records), "providers": records}

    def client(self, profile: ProviderProfile) -> "ConfiguredModelClient":
        if profile.provider_type == "mock":
            return ConfiguredModelClient(profile, None)
        if not self.allow_real_api:
            raise PermissionError("allow_real_api=true is required")
        key = resolve_secret(alias=profile.api_key_alias, env_name=profile.api_key_env)
        if not key:
            raise PermissionError("configured credential is missing")
        sdk = OpenAI(api_key=key, base_url=profile.base_url, timeout=profile.timeout_seconds, max_retries=0)
        return ConfiguredModelClient(profile, sdk)


class ConfiguredModelClient:
    def __init__(self, profile: ProviderProfile, sdk: Any) -> None:
        self.profile = profile
        self.sdk = sdk
        self.last_request_metrics: dict[str, Any] = {}

    def _wait(self) -> None:
        if self.profile.rate_limit and self.profile.rate_limit > 0:
            time.sleep(60.0 / self.profile.rate_limit)

    def _request(self, operation: Any, *, request_descriptor: dict[str, Any],
                 attempt_ledger_path: Path | None = None,
                 attempt_context: dict[str, Any] | None = None) -> Any:
        last: Exception | None = None
        started_at = _utc_now()
        started = time.monotonic()
        context = dict(attempt_context or {})
        if attempt_ledger_path is not None:
            required_context = {"project_id", "job_id", "stage", "provider_role", "page_or_segment_id", "purpose"}
            missing = required_context - set(context)
            if missing:
                raise ValueError(f"provider attempt context is missing fields: {sorted(missing)}")
        attempt_id = f"provider_attempt_{hashlib.sha256(f'{started_at}:{id(self)}'.encode()).hexdigest()[:24]}"
        request_hash = _json_hash(request_descriptor)
        for attempt in range(self.profile.max_retries + 1):
            attempt_started_at = _utc_now()
            attempt_started = time.monotonic()
            dispatch = {
                "attempt_id": attempt_id, **context, "provider": self.profile.provider_id,
                "model": self.profile.model, "start": attempt_started_at, "end": None, "latency": None,
                "status": "dispatching", "retry_number": attempt, "usage": {},
                "request_hash": request_hash, "response_hash": None, "error_code": None,
            }
            _append_provider_attempt(attempt_ledger_path, dispatch)
            try:
                response = operation()
                usage = getattr(response, "usage", None)
                if hasattr(usage, "model_dump"):
                    usage = usage.model_dump(mode="json")
                elif usage is None:
                    usage = {}
                self.last_request_metrics = {
                    "start": started_at,
                    "end": _utc_now(),
                    "latency": time.monotonic() - started,
                    "retry_number": attempt,
                    "usage": usage,
                    "status": "succeeded",
                }
                raw_response = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
                _append_provider_attempt(attempt_ledger_path, {
                    **dispatch, "end": _utc_now(), "latency": time.monotonic() - attempt_started,
                    "status": "succeeded", "usage": usage, "response_hash": _json_hash(raw_response),
                    "error_code": None,
                })
                return response
            except Exception as exc:
                last = exc
                status = getattr(exc, "status_code", None)
                failed = {
                    **dispatch, "end": _utc_now(), "latency": time.monotonic() - attempt_started,
                    "status": "failed", "usage": {}, "response_hash": None,
                    "error_code": type(exc).__name__,
                }
                _append_provider_attempt(attempt_ledger_path, failed)
                if status is not None and int(status) not in {408, 409, 429} and int(status) < 500:
                    self.last_request_metrics = {
                        "start": started_at, "end": _utc_now(), "latency": time.monotonic() - started,
                        "retry_number": attempt, "usage": {}, "status": "failed",
                        "error_code": type(exc).__name__,
                    }
                    raise
                if attempt >= self.profile.max_retries:
                    self.last_request_metrics = {
                        "start": started_at, "end": _utc_now(), "latency": time.monotonic() - started,
                        "retry_number": attempt, "usage": {}, "status": "failed",
                        "error_code": type(exc).__name__,
                    }
                    raise
                time.sleep(min(30.0, 2.0 ** attempt))
        raise RuntimeError("provider request failed") from last

    def text_json(self, *, system_prompt: str, payload: dict[str, Any],
                  attempt_ledger_path: Path | None = None,
                  attempt_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.profile.provider_type == "mock":
            now = _utc_now()
            self.last_request_metrics = {"start": now, "end": now, "latency": 0.0,
                                         "retry_number": 0, "usage": {}, "status": "succeeded"}
            return {"model": self.profile.model, "choices": [{"message": {"content": json.dumps(payload)}}]}
        self._wait()
        response = self._request(lambda: self.sdk.chat.completions.create(
            model=self.profile.model, messages=[{"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            response_format={"type": "json_object"}, temperature=float(self.profile.extra.get("temperature", 0)),
            max_tokens=int(self.profile.extra.get("max_output_tokens", 8192)),
            # Faithful unit translation does not benefit from spending the
            # entire output budget on hidden reasoning.  Some compatible APIs
            # default thinking to enabled, so keep the validated Phase 13.5
            # policy explicit and configurable.
            extra_body={"thinking": {"type": str(self.profile.extra.get("thinking_mode", "disabled"))}}),
            request_descriptor={"kind": "text_json", "system_prompt": system_prompt, "payload": payload},
            attempt_ledger_path=attempt_ledger_path, attempt_context=attempt_context)
        return response.model_dump(mode="json")

    def vision_json(self, *, prompt: str, image_path: Path,
                    attempt_ledger_path: Path | None = None,
                    attempt_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.profile.provider_type == "mock":
            now = _utc_now()
            self.last_request_metrics = {"start": now, "end": now, "latency": 0.0,
                                         "retry_number": 0, "usage": {}, "status": "succeeded"}
            return {"model": self.profile.model, "choices": [{"message": {"content": "{\"pages\": []}"}}]}
        self._wait()
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        response = self._request(lambda: self.sdk.chat.completions.create(
            model=self.profile.model, messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                # This native multimodal profile expects a bare base64 payload
                # for local images; data URLs are not universally compatible.
                {"type": "image_url", "image_url": {"url": encoded}},
            ]}],
            temperature=float(self.profile.extra.get("temperature", 0)),
            max_tokens=int(self.profile.extra.get("max_output_tokens", 8192)),
        ), request_descriptor={"kind": "vision_json", "prompt": prompt,
                               "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()},
            attempt_ledger_path=attempt_ledger_path, attempt_context=attempt_context)
        return response.model_dump(mode="json")


def parse_model_json(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices") or []
    if not choices:
        raise ValueError("provider response has no choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("provider response has no content")
    value = content.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1]).strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("provider JSON content must be an object")
    return parsed


class RegistryTranslationProvider:
    """Translation adapter with append-only, raw-first attempts."""

    def __init__(self, workspace: Path, client: ConfiguredModelClient,
                 *, control: Callable[[], None] | None = None,
                 attempt_ledger_path: Path | None = None,
                 attempt_context: dict[str, Any] | None = None) -> None:
        self.workspace = workspace.resolve()
        self.client = client
        self.control = control
        self.attempt_ledger_path = attempt_ledger_path
        self.attempt_context = dict(attempt_context or {})

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider_id": self.client.profile.provider_id,
                "model": self.client.profile.model}

    def estimate_request(self, units: list[dict[str, Any]]) -> dict[str, Any]:
        return {"units": len(units), "characters": sum(len(x["source_text"]) for x in units)}

    def translate_batch(self, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        attempts_path = self.workspace / "logs/text_attempts.jsonl"
        attempts = [json.loads(x) for x in attempts_path.read_text("utf-8").splitlines() if x.strip()] if attempts_path.is_file() else []
        results = []

        def record(item: dict[str, Any]) -> None:
            attempts_path.parent.mkdir(parents=True, exist_ok=True)
            with attempts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            attempts.append(item)

        document_terms = _workspace_protected_document_terms(self.workspace, units)
        for unit in units:
            if self.control:
                self.control()
            source_text = str(unit["source_text"])
            protected_terms = tuple(dict.fromkeys([
                *(unit.get("protected_terms") or ()),
                *(term for term in document_terms
                  if re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", source_text)),
                *_protected_literal_terms(source_text),
            ]))
            quality_unit = {**unit, "protected_terms": protected_terms}
            note_contract = _segmented_note_contract(unit["source_text"])
            table_contract = (
                _structured_table_contract(unit["source_text"])
                if unit.get("preserve_structure") else None
            )
            structure_instruction = (
                " Preserve every table row boundary and every tab or Markdown pipe delimiter exactly; "
                "translate cell text only."
                if unit.get("preserve_structure") else ""
            )
            if table_contract:
                request_payload = {
                    "translation_unit_id": unit["translation_unit_id"],
                    "source_segments": table_contract["source_segments"],
                }
                system_prompt = (
                    f"Translate faithfully from {unit['source_language']} to {unit['target_language']}. "
                    "Return JSON with translated_segments as an array of objects with the exact same integer id "
                    "and translated cell text. Translate every cell; do not add, remove, merge, or reorder ids. "
                    "Preserve proper names. Markdown delimiters, row boundaries, numbers, and note markers are "
                    "reconstructed separately."
                )
            elif note_contract:
                request_payload = {
                    "translation_unit_id": unit["translation_unit_id"],
                    "source_segments": [
                        {"id": index, "text": text}
                        for index, text in enumerate(note_contract["segments"]) if text.strip()
                    ],
                }
                system_prompt = (
                    f"Translate faithfully from {unit['source_language']} to {unit['target_language']}. "
                    "Return JSON with translated_segments as an array of objects with the exact same integer id "
                    "and translated text. Do not add, remove, merge, or reorder segment ids. Every translated "
                    "segment must actually be written in the target language."
                    + structure_instruction
                )
            else:
                request_payload = {"translation_unit_id": unit["translation_unit_id"],
                                   "source_text": unit["source_text"]}
                system_prompt = (
                    f"Translate faithfully from {unit['source_language']} to {unit['target_language']}. "
                    "Return JSON with translated_text. Preserve numbers, units, and names. "
                    "The translated_text must actually be written in the target language; never copy an "
                    "entire source-language sentence merely because it contains names or technical terms."
                    + structure_instruction
                )
            if protected_terms:
                request_payload["protected_terms"] = list(protected_terms)
                system_prompt += (
                    " Preserve these document identifiers exactly, including spelling and case: "
                    + ", ".join(protected_terms) + "."
                )
            protected_markers = _protected_term_markers(protected_terms)
            if protected_markers:
                if "source_text" in request_payload:
                    request_payload["source_text"] = _mask_protected_terms(
                        str(request_payload["source_text"]), protected_markers,
                    )
                for segment in request_payload.get("source_segments", []):
                    segment["text"] = _mask_protected_terms(
                        str(segment["text"]), protected_markers,
                    )
                system_prompt += (
                    " Preserve every {{PROTECTED_TERM:n}} placeholder exactly; it is restored after translation."
                )
            fingerprint_payload = {
                "unit": unit["translation_unit_id"], "source_sha256": unit["source_text_sha256"],
                "provider": self.client.profile.provider_id, "model": self.client.profile.model,
                "source_language": unit["source_language"], "target_language": unit["target_language"],
                "protected_terms": list(protected_terms),
            }
            if protected_markers:
                fingerprint_payload["protected_term_contract"] = "placeholder-v1"
            if table_contract or note_contract:
                fingerprint_payload["translation_contract"] = (table_contract or note_contract)["version"]
            fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
            recovered = next((item for item in reversed(attempts) if item.get("fingerprint") == fingerprint
                              and item.get("raw_path") and Path(item["raw_path"]).is_file()), None)
            if recovered is not None:
                raw = json.loads(Path(recovered["raw_path"]).read_text("utf-8"))
                try:
                    parsed = parse_model_json(raw)
                    canonical = _normalize_unit_translation(
                        parsed, unit,
                        allow_text_alias=bool(self.client.profile.extra.get("allow_text_alias", False)),
                    )
                    canonical.translated_text = _restore_protected_terms(
                        canonical.translated_text, protected_markers,
                    )
                    _validate_translation_fidelity(quality_unit, canonical.translated_text)
                except (ValueError, json.JSONDecodeError, ProviderSchemaError):
                    canonical = None
                if canonical is not None:
                    final = {**recovered, "status": "validated", "recovered_offline": True,
                             "response_hash": _json_hash(raw), "end": _utc_now(), "latency": 0.0}
                    record(final)
                    results.append({"translation_unit_id": unit["translation_unit_id"],
                                    "translated_text": canonical.translated_text, "status": "translated"})
                    continue
            quality_attempt_limit = max(
                2, min(4, int(getattr(self.client.profile, "max_retries", 1)) + 1)
            )
            for quality_attempt in range(quality_attempt_limit):
                attempt_id = f"text-{len(attempts) + 1:06d}-{fingerprint[:12]}-q{quality_attempt}"
                attempt_payload = dict(request_payload)
                attempt_prompt = system_prompt
                if quality_attempt:
                    attempt_payload["quality_retry"] = {
                        "attempt": quality_attempt,
                        "reason": "previous response failed target-language fidelity validation",
                    }
                    attempt_prompt += (
                        " A previous response failed target-language fidelity validation. Translate every source "
                        "sentence and table cell now; return no source-language prose except proper names."
                    )
                dispatch = {
                    "attempt_id": attempt_id, "fingerprint": fingerprint, "status": "dispatching",
                    "project_id": unit.get("project_id"), "job_id": unit.get("job_id"), "stage": "translation",
                    "provider_role": "language", "provider": self.client.profile.provider_id,
                    "provider_id": self.client.profile.provider_id, "model": self.client.profile.model,
                    "page_or_segment_id": unit["translation_unit_id"],
                    "translation_unit_id": unit["translation_unit_id"],
                    "purpose": f"{unit['source_language']}->{unit['target_language']}", "start": _utc_now(),
                    "retry_number": quality_attempt, "usage": {}, "request_hash": _json_hash(attempt_payload),
                    "response_hash": None, "error_code": None, "created_at": _utc_now(),
                }
                record(dispatch)
                try:
                    raw = self.client.text_json(
                        system_prompt=attempt_prompt, payload=attempt_payload,
                        attempt_ledger_path=self.attempt_ledger_path,
                        attempt_context={
                            **self.attempt_context,
                            "page_or_segment_id": unit["translation_unit_id"],
                            "purpose": (
                                f"translation:{unit['source_language']}->{unit['target_language']}"
                                if quality_attempt == 0 else
                                f"translation:{unit['source_language']}->{unit['target_language']}:quality_retry_{quality_attempt}"
                            ),
                            "quality_retry_number": quality_attempt,
                        },
                    )
                except Exception as exc:
                    metrics = dict(self.client.last_request_metrics)
                    record({**dispatch, **metrics, "status": "failed", "error_code": type(exc).__name__})
                    raise
                if self.control:
                    self.control()
                raw_path = self.workspace / "logs/text/raw" / f"{attempt_id}.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = raw_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", "utf-8")
                temporary.replace(raw_path)
                metrics = dict(self.client.last_request_metrics)
                saved = {**dispatch, **metrics, "status": "raw_saved", "raw_path": str(raw_path),
                         "request_id": raw.get("id"), "response_hash": _json_hash(raw)}
                record(saved)
                try:
                    parsed = parse_model_json(raw)
                    canonical = _normalize_unit_translation(
                        parsed, unit,
                        allow_text_alias=bool(self.client.profile.extra.get("allow_text_alias", False)),
                    )
                    canonical.translated_text = _restore_protected_terms(
                        canonical.translated_text, protected_markers,
                    )
                    _validate_translation_fidelity(quality_unit, canonical.translated_text)
                except (ValueError, json.JSONDecodeError, ProviderSchemaError) as exc:
                    schema_error = exc if isinstance(exc, ProviderSchemaError) else ProviderSchemaError(
                        f"provider_schema_error: {exc}"
                    )
                    record({**saved, "status": "failed", "error_code": "provider_schema_error",
                            "schema_error": str(schema_error), "end": _utc_now()})
                    if quality_attempt + 1 < quality_attempt_limit:
                        continue
                    raise schema_error from exc
                results.append({"translation_unit_id": unit["translation_unit_id"],
                                "translated_text": canonical.translated_text, "status": "translated"})
                final = {**saved, "status": "validated", "end": _utc_now(), "error_code": None}
                record(final)
                break
        return results

    def normalize_response(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        values = response.get("translations")
        if not isinstance(values, list):
            raise ValueError("malformed provider response")
        return values
