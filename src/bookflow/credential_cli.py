"""Non-contract desktop helper for Windows credential storage.

The secret is accepted only on stdin and the JSON response never contains it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .credential_store import CredentialStore


def _alias_for_role(role: str, provider_config: Path) -> str:
    data = yaml.safe_load(provider_config.read_text("utf-8")) or {}
    active_field = "active_translation_provider" if role == "language" else "active_vision_provider"
    provider_id = data.get(active_field)
    profile = (data.get("providers") or {}).get(provider_id) if provider_id else None
    alias = profile.get("api_key_alias") if isinstance(profile, dict) else None
    if not isinstance(alias, str) or not alias.strip():
        raise ValueError(f"credential alias is not configured for the {role} model role")
    return alias.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("status", "set", "delete"), required=True)
    reference = parser.add_mutually_exclusive_group(required=True)
    reference.add_argument("--alias")
    reference.add_argument("--role", choices=("language", "vision"))
    parser.add_argument("--provider-config", type=Path)
    args = parser.parse_args()
    alias = args.alias
    if args.role:
        if args.provider_config is None:
            raise ValueError("--provider-config is required with --role")
        alias = _alias_for_role(args.role, args.provider_config.resolve())
    assert alias is not None
    store = CredentialStore()
    if args.action == "status":
        result = store.test(alias)
    elif args.action == "set":
        secret = sys.stdin.read()
        if not secret:
            raise ValueError("credential must not be empty")
        result = store.set(alias, secret)
    else:
        result = store.delete(alias)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
