from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json


def cache_fingerprint(spec: dict[str, Any]) -> str:
    fields = ["source_text_sha256", "source_object_id", "source_language", "target_language",
              "translation_policy", "provider", "model", "prompt_version", "glossary_version",
              "output_schema_version"]
    payload = {key: spec.get(key) for key in fields}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class TranslationCache:
    def __init__(self, root: Path):
        self.root = root / "data/fullbook/multilingual/cache"

    def get(self, fingerprint: str, source_sha: str) -> dict | None:
        path = self.root / fingerprint[:2] / f"{fingerprint}.json"
        if not path.is_file(): return None
        value = json.loads(path.read_text("utf-8"))
        return value if value.get("source_text_sha256") == source_sha else None

    def put(self, fingerprint: str, value: dict) -> Path:
        path = self.root / fingerprint[:2] / f"{fingerprint}.json"
        if path.exists():
            existing = json.loads(path.read_text("utf-8"))
            if existing != value: raise ValueError("immutable cache collision")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, value)
        return path
