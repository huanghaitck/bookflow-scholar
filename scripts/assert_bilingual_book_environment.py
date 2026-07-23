"""Fail closed unless Bookflow is running in its dedicated Conda environment."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PREFIX = Path(
    os.environ.get(
        "BOOKFLOW_EXPECTED_PYTHON_PREFIX",
        Path.home() / ".conda" / "envs" / "bilingual-book",
    )
)
EXPECTED_EXECUTABLE = EXPECTED_PREFIX / "python.exe"
REQUIRED_IMPORTS = ("fitz", "PIL", "pydantic", "yaml", "pytest", "bookflow")


def normalized(path: Path) -> str:
    return str(path.resolve()).replace("/", "\\").casefold()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="Only use the exit code.")
    args = parser.parse_args()

    failures: list[str] = []
    executable = Path(sys.executable)
    prefix = Path(sys.prefix)
    if normalized(executable) != normalized(EXPECTED_EXECUTABLE):
        failures.append("unexpected_python_executable")
    if normalized(prefix) != normalized(EXPECTED_PREFIX):
        failures.append("unexpected_python_prefix")
    if sys.version_info[:2] != (3, 12):
        failures.append("python_version_must_be_3_12")

    imports: dict[str, str] = {}
    for name in REQUIRED_IMPORTS:
        try:
            module = importlib.import_module(name)
            imports[name] = str(getattr(module, "__file__", "built-in"))
        except Exception as exc:  # pragma: no cover - exercised by wrong environments
            imports[name] = type(exc).__name__
            failures.append(f"import_failed:{name}")

    bookflow_path = imports.get("bookflow", "")
    if bookflow_path and not normalized(Path(bookflow_path)).startswith(
        normalized(PROJECT_ROOT / "src" / "bookflow")
    ):
        failures.append("bookflow_imported_from_unexpected_location")

    result = {
        "ok": not failures,
        "sys_executable": str(executable),
        "sys_prefix": str(prefix),
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "expected_environment": str(EXPECTED_PREFIX),
        "project_root": str(PROJECT_ROOT),
        "imports": imports,
        "failures": failures,
    }
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
