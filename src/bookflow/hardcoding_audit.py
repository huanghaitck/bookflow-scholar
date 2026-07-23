"""Deterministic hardcoding gate for the installable, general Bookflow runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PATTERNS = (
    "Journal de mon troisième voyage", "french_real_workspace", "D:\\bookflow_validation",
    "fr -> zh-Hans", "physical_page_count == 374", "DeepSeek", "GLM",
    "C:\\Program Files\\LibreOffice", "D:\\books\\source.pdf", "D:\\books\\output",
)
RUNTIME_FILES = (
    "credential_store.py", "provider_registry.py", "multilingual_workspace.py",
    "publication_structure.py", "manual_review.py", "renderer_backend.py",
    "runtime_cli.py", "ui_server.py",
)


def audit_runtime(package_root: Path) -> dict[str, Any]:
    package_root = package_root.resolve()
    paths = [package_root / name for name in RUNTIME_FILES]
    paths.extend((package_root / "ui_prototypes").rglob("*.html"))
    paths.extend((package_root / "ui_prototypes" / "assets").glob("*.js"))
    findings = []
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            for pattern in PATTERNS:
                if pattern.casefold() in line.casefold():
                    findings.append({"path": str(path.relative_to(package_root.parent.parent)),
                                     "line": line_number, "pattern": pattern})
    return {"schema_version": "bookflow-hardcoding-audit-v1",
            "production_hardcoding_findings": len(findings), "findings": findings,
            "scanned_files": len([path for path in paths if path.is_file()])}


def write_audit(package_root: Path, output: Path) -> dict[str, Any]:
    result = audit_runtime(package_root)
    output.mkdir(parents=True, exist_ok=True)
    allowlist = {"schema_version": "bookflow-hardcoding-allowlist-v1", "entries": [],
                 "policy": "Only tests, validation configs, reports, and evidence may be allowlisted."}
    (output / "hardcoding_findings.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    (output / "hardcoding_allowlist.json").write_text(json.dumps(allowlist, indent=2) + "\n", "utf-8")
    lines = ["# Hardcoding audit", "", f"- Scanned files: `{result['scanned_files']}`",
             f"- Production findings: `{result['production_hardcoding_findings']}`", ""]
    if result["findings"]:
        lines.extend(f"- `{item['path']}:{item['line']}`: `{item['pattern']}`" for item in result["findings"])
    else:
        lines.append("No prohibited current-book, workspace, model, or renderer defaults were found.")
    (output / "hardcoding_audit.md").write_text("\n".join(lines) + "\n", "utf-8")
    return result
