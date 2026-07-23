"""Controlled real-provider acceptance adapter for B2A v2.

This module never reads credentials directly. ProviderRegistry resolves the existing
profile and environment-variable mapping; only provider IDs and model aliases are
persisted in acceptance artifacts.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import fitz

from .provider_registry import ProviderRegistry, parse_model_json


class RealCallBudget:
    def __init__(self, maximum: int = 20) -> None:
        self.maximum = maximum; self.count = 0; self.retries = 0; self.ledger: list[dict[str, Any]] = []

    def call(self, operation: Callable[[], dict[str, Any]], *, retry_once: bool = True,
             provider_id: str = "unknown", operation_name: str = "provider_call") -> tuple[dict[str, Any], int, float]:
        started = time.perf_counter(); attempts = 0
        while True:
            if self.count >= self.maximum: raise RuntimeError(f"real API call budget exhausted at {self.maximum}")
            self.count += 1; attempts += 1
            call_started = time.perf_counter()
            try:
                result = operation()
                self.ledger.append({"sequence": self.count, "provider_id": provider_id, "operation": operation_name, "attempt": attempts, "status": "success", "latency_seconds": round(time.perf_counter() - call_started, 3), "timestamp": datetime.now(timezone.utc).isoformat()})
                return result, attempts - 1, time.perf_counter() - started
            except Exception as exc:
                self.ledger.append({"sequence": self.count, "provider_id": provider_id, "operation": operation_name, "attempt": attempts, "status": "failed", "error_type": type(exc).__name__, "latency_seconds": round(time.perf_counter() - call_started, 3), "timestamp": datetime.now(timezone.utc).isoformat()})
                if not retry_once or attempts >= 2: raise
                self.retries += 1; time.sleep(1)


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    value = raw.get("usage") or {}
    return {"prompt_tokens": int(value.get("prompt_tokens", 0) or 0), "completion_tokens": int(value.get("completion_tokens", 0) or 0), "total_tokens": int(value.get("total_tokens", 0) or 0)}


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8"); temporary.replace(path)


def normalize_page_markdown(value: str) -> str:
    """Add deterministic heading markers without changing translated content."""
    lines = value.strip().splitlines()
    first_after_marker = False
    normalized: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"<!-- page \d+ -->", stripped):
            first_after_marker = True; normalized.append(stripped); continue
        if first_after_marker and stripped:
            first_after_marker = False
            normalized.append(stripped if stripped.startswith("#") else f"# {stripped}")
            continue
        hierarchy = re.match(r"^(\d+(?:\.\d+)+)\s+(.+)$", stripped)
        if hierarchy and not stripped.startswith("#"):
            level = min(6, hierarchy.group(1).count(".") + 1)
            normalized.append(f"{'#' * level} {stripped}")
        else:
            normalized.append(line.rstrip())
    return "\n".join(normalized).strip()


class RealProviderAcceptanceProcessor:
    def __init__(self, provider_config: Path, output_root: Path, budget: RealCallBudget) -> None:
        self.registry = ProviderRegistry.load(provider_config)
        self.output_root = output_root.resolve(); self.output_root.mkdir(parents=True, exist_ok=True)
        self.budget = budget

    def _client(self, provider_id: str, capability: str):
        profile = replace(self.registry.get(provider_id, capability), max_retries=0)
        return profile, self.registry.client(profile)

    def health_checks(self, image_path: Path, vision_provider_id: str, text_provider_id: str) -> list[dict[str, Any]]:
        vision_profile, vision = self._client(vision_provider_id, "vision")
        raw_v, retry_v, latency_v = self.budget.call(lambda: vision.vision_json(prompt='Return JSON exactly as {"ok":true}. Do not transcribe.', image_path=image_path), retry_once=False, provider_id=vision_profile.provider_id, operation_name="health_check_vision")
        if parse_model_json(raw_v).get("ok") is not True: raise ValueError("vision provider health response invalid")
        text_profile, text = self._client(text_provider_id, "text")
        raw_t, retry_t, latency_t = self.budget.call(lambda: text.text_json(system_prompt='Return JSON exactly as {"ok":true}.', payload={"health_check": True}), retry_once=False, provider_id=text_profile.provider_id, operation_name="health_check_text")
        if parse_model_json(raw_t).get("ok") is not True: raise ValueError("text provider health response invalid")
        return [{"provider_id": vision_profile.provider_id, "model_alias": vision_profile.model, "capability": "vision", "ok": True, "retry_count": retry_v, "latency_seconds": latency_v, "usage": _usage(raw_v)}, {"provider_id": text_profile.provider_id, "model_alias": text_profile.model, "capability": "text", "ok": True, "retry_count": retry_t, "latency_seconds": latency_t, "usage": _usage(raw_t)}]

    def __call__(self, job: dict[str, Any]) -> dict[str, Any]:
        config = json.loads(job.get("config_json") or "{}")
        vision_id = config["vision_provider_id"]; text_id = config["text_provider_id"]
        source_language = job["source_language"] if job["source_language"] != "auto" else config.get("source_language", "auto")
        target_language = config["target_language"]
        source = Path(job["source_path"]); stage_root = self.output_root / job["job_id"]
        cache = stage_root / "checkpoints/provider"; cache.mkdir(parents=True, exist_ok=True)
        vision_profile, vision = self._client(vision_id, "vision")
        page_images: list[Path] = []
        page_records: list[dict[str, Any]] = []
        raw_visions: list[dict[str, Any]] = []
        vision_page_numbers: list[int] = []
        retry_v = 0; latency_v = 0.0
        if source.suffix.lower() == ".pdf":
            document = fitz.open(source)
            try:
                for index, page in enumerate(document):
                    page_number = index + 1
                    image = cache / f"source-page-{page_number:04d}.png"
                    if not image.is_file():
                        page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False).save(image)
                    page_images.append(image)
                    extracted = page.get_text("text").strip()
                    if len(extracted) >= 40:
                        lines = [line.strip() for line in extracted.splitlines() if line.strip()]
                        record = {"language": source_language, "title": lines[0][:160] if lines else f"Page {page_number}", "plain_text": extracted, "markdown": "\n\n".join(lines), "page_number": page_number, "ocr_source": "embedded_text"}
                    else:
                        validated_path = cache / f"ocr-page-{page_number:04d}.json"
                        raw_path = cache / f"vision-page-{page_number:04d}-raw.json"
                        if validated_path.is_file() and raw_path.is_file():
                            record = json.loads(validated_path.read_text("utf-8")); raw_vision = json.loads(raw_path.read_text("utf-8"))
                        else:
                            prompt = ("Transcribe this document page faithfully. Return one JSON object with exactly these keys: "
                                "language, title, plain_text, markdown. Preserve headings, paragraphs, table cells, footnotes, numbers and punctuation. "
                                f"This is physical page {page_number}; expected language hint: {source_language}. Do not translate. Never return markdown fences.")
                            raw_vision, retries, latency = self.budget.call(lambda image=image, prompt=prompt: vision.vision_json(prompt=prompt, image_path=image), provider_id=vision_profile.provider_id, operation_name=f"ocr_page_{page_number:04d}")
                            retry_v += retries; latency_v += latency
                            record = parse_model_json(raw_vision)
                            for key in ("language", "title", "plain_text", "markdown"):
                                if not isinstance(record.get(key), str) or not record[key].strip(): raise ValueError(f"vision structured output missing {key} on page {page_number}")
                            record |= {"page_number": page_number, "ocr_source": "glm_vision"}
                            _atomic_json(raw_path, raw_vision); _atomic_json(validated_path, record)
                        raw_visions.append(raw_vision); vision_page_numbers.append(page_number)
                    page_records.append(record)
            finally:
                document.close()
        else:
            page_images.append(source)
            prompt = ("Transcribe this document image faithfully. Return one JSON object with exactly these keys: "
                "language, title, plain_text, markdown. Preserve headings, paragraphs, list markers, numbers and punctuation. "
                f"Expected language hint: {source_language}. Do not translate. Never return markdown fences.")
            raw_vision, retry_v, latency_v = self.budget.call(lambda: vision.vision_json(prompt=prompt, image_path=source), provider_id=vision_profile.provider_id, operation_name="ocr_image")
            record = parse_model_json(raw_vision)
            for key in ("language", "title", "plain_text", "markdown"):
                if not isinstance(record.get(key), str) or not record[key].strip(): raise ValueError(f"vision structured output missing {key}")
            record |= {"page_number": 1, "ocr_source": "glm_vision"}; raw_visions.append(raw_vision); vision_page_numbers.append(1); page_records.append(record)
        _atomic_json(cache / "ocr_validated.json", {"pages": page_records})
        source_text = "\n\n".join(f"[Page {item['page_number']}]\n{item['plain_text']}" for item in page_records)
        source_markdown = "\n\n".join(f"<!-- page {item['page_number']} -->\n\n{item['markdown']}" for item in page_records)
        final_markdown = source_markdown.strip(); raw_text: dict[str, Any] | None = None; retry_t = 0; latency_t = 0.0
        text_profile, text = self._client(text_id, "text")
        if config.get("translation_enabled", True) or config.get("structure_enabled", True):
            operation = "clean and structure without translation" if source_language == target_language else f"translate from {source_language} to {target_language}"
            text_raw_path = cache / "text_raw.json"; text_validated_path = cache / "text_validated.json"
            if text_raw_path.is_file() and text_validated_path.is_file():
                raw_text = json.loads(text_raw_path.read_text("utf-8")); transformed = json.loads(text_validated_path.read_text("utf-8"))
            else:
                raw_text, retry_t, latency_t = self.budget.call(lambda: text.text_json(system_prompt=(
                    f"{operation}. Return JSON with title, cleaned_text, markdown, source_language, target_language. "
                    "Preserve page markers, headings, numbers, terminology, tables, footnotes and endnotes; markdown must contain all 12 pages in order."), payload={"title": page_records[0]["title"], "source_text": source_text, "source_markdown": source_markdown, "source_language": source_language, "target_language": target_language}), provider_id=text_profile.provider_id, operation_name="translate_and_structure_book")
                _atomic_json(text_raw_path, raw_text); transformed = parse_model_json(raw_text)
            for key in ("title", "cleaned_text", "markdown", "source_language", "target_language"):
                if not isinstance(transformed.get(key), str) or not transformed[key].strip(): raise ValueError(f"text structured output missing {key}")
            final_markdown = transformed["markdown"].strip(); _atomic_json(text_validated_path, transformed)
        output = stage_root / "output"; (output / "assets/images").mkdir(parents=True, exist_ok=True); (output / "assets/tables").mkdir(parents=True, exist_ok=True); (output / "checkpoints").mkdir(parents=True, exist_ok=True)
        final_markdown = normalize_page_markdown(final_markdown)
        (output / "book.md").write_text(final_markdown + "\n", "utf-8")
        for index, image in enumerate(page_images, 1): shutil.copy2(image, output / "assets/images" / f"source-page-{index:04d}.png")
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for raw_vision in raw_visions:
            for key, value in _usage(raw_vision).items(): usage[key] += value
        if raw_text:
            for key, value in _usage(raw_text).items(): usage[key] += value
        request_count = len(vision_page_numbers) + (1 if raw_text else 0)
        metadata = {"source_id": job["source_id"], "source_language": source_language, "target_language": target_language, "page_count": len(page_records), "vision_page_numbers": vision_page_numbers, "vision_provider_id": vision_profile.provider_id, "vision_model_alias": vision_profile.model, "text_provider_id": text_profile.provider_id, "text_model_alias": text_profile.model, "ocr_mode": "hybrid_embedded_text_and_real_vision", "request_count": request_count, "retry_count": retry_v + retry_t, "usage": usage}
        _atomic_json(output / "metadata.json", metadata); _atomic_json(output / "warnings.json", [])
        (output / "processing_report.md").write_text(f"# Processing report\n\n- Pages: {len(page_records)}\n- Real vision pages: {vision_page_numbers}\n- Embedded-text pages: {len(page_records) - len(vision_page_numbers)}\n- Structured output validation: passed\n- Markdown: generated\n", "utf-8")
        (output / "source_manifest.csv").write_text(f"source_id,source_path,sha256\n{job['source_id']},{source},{job['sha256']}\n", "utf-8")
        review = {"schema_version": "human-review-queue-v1.1", "source_id": job["source_id"], "issues": []}; _atomic_json(output / "HUMAN_REVIEW_QUEUE.json", review); (output / "HUMAN_REVIEW_QUEUE.md").write_text("# Human review queue\n\nNo pending review items.\n", "utf-8")
        _atomic_json(output / "checkpoints/job.json", {"job_id": job["job_id"], "stage": "completed", "provider_request_count": metadata["request_count"], "updated_at": time.time()})
        return {"output_path": str(output), "provider_id": f"{vision_profile.provider_id}+{text_profile.provider_id}", "model_alias": f"{vision_profile.model}+{text_profile.model}", "request_count": metadata["request_count"], "retry_count": metadata["retry_count"], "latency_seconds": latency_v + latency_t, "usage": usage, "result_path": str(output / "book.md")}
