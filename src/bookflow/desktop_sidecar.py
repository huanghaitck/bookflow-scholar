"""Unified entry point for the packaged desktop sidecar."""

from __future__ import annotations

import sys

from . import bridge_cli, credential_cli


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"bridge", "credential"}:
        sys.stderr.write("usage: bookflow-sidecar {bridge|credential} [arguments]\n")
        return 2
    mode = sys.argv.pop(1)
    if mode == "bridge":
        return bridge_cli.main()
    return credential_cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
