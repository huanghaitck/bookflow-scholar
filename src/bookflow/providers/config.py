from __future__ import annotations

import os
from pathlib import Path
import yaml


def _load_project_env(path: Path) -> None:
    """Load the project-local .env without overwriting caller variables."""
    env_path = path.resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text("utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def _expand_env(value):
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def load_provider_config(path: Path) -> dict:
    _load_project_env(path)
    data = _expand_env(yaml.safe_load(path.read_text("utf-8")) or {})
    if data.get("allow_real_api") not in {False, True}: raise ValueError("allow_real_api must be boolean")
    for config in data.get("providers", {}).values():
        if "api_key" in config: raise ValueError("literal API keys are forbidden")
        env_name = config.get("api_key_env")
        if env_name: config["api_key_available"] = bool(os.getenv(env_name))
    return data
