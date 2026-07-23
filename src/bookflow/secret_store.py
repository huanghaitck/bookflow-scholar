"""Secret lookup helpers that never expose secret values in reports."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from .paths import ProjectSettings


def configured_key_names(settings: ProjectSettings) -> list[str]:
    names = [settings.vision_api_key_env, *settings.vision_compatible_api_key_envs]
    return list(dict.fromkeys(name for name in names if name))


def api_key_status(settings: ProjectSettings, root: Path) -> tuple[bool, str | None]:
    """Return only presence and matched variable name, never the value."""

    names = configured_key_names(settings)
    for name in names:
        if name in os.environ and bool(os.environ[name]):
            return True, name
    env_path = root / ".env"
    if not env_path.is_file():
        return False, None
    values = dotenv_values(env_path)
    for name in names:
        if bool(values.get(name)):
            return True, name
    return False, None


def load_api_key(settings: ProjectSettings, root: Path) -> tuple[str, str]:
    """Load one configured key for an approved call without logging it."""

    names = configured_key_names(settings)
    for name in names:
        value = os.environ.get(name)
        if value:
            return value, name
    env_path = root / ".env"
    if env_path.is_file():
        values = dotenv_values(env_path)
        for name in names:
            value = values.get(name)
            if value:
                return str(value), name
    raise RuntimeError(
        f"API key is not configured. Set {settings.vision_api_key_env} locally."
    )


def translation_api_key_status(
    settings: ProjectSettings, root: Path
) -> tuple[bool, str | None]:
    """Return only whether the configured translation key exists."""

    name = settings.translation_api_key_env
    if bool(os.environ.get(name)):
        return True, name
    env_path = root / ".env"
    if not env_path.is_file():
        return False, None
    values = dotenv_values(env_path)
    return (True, name) if bool(values.get(name)) else (False, None)


def load_translation_api_key(settings: ProjectSettings, root: Path) -> tuple[str, str]:
    """Load the translation key only at an already-approved call boundary."""

    name = settings.translation_api_key_env
    value = os.environ.get(name)
    if value:
        return value, name
    env_path = root / ".env"
    if env_path.is_file():
        stored = dotenv_values(env_path).get(name)
        if stored:
            return str(stored), name
    raise RuntimeError(f"API key is not configured. Set {name} locally.")
