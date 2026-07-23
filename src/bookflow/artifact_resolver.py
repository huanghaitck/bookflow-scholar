"""Canonical, project-scoped asset and artifact manifests for the desktop client."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .io_utils import atomic_write_json, sha256_file


ARTIFACT_MANIFEST_NAME = "artifact_manifest.json"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class ResolvedItem:
    identifier: str
    path: Path
    role: str
    mime_type: str
    sha256: str
    build_id: str | None = None


class WorkspacePathResolver:
    def __init__(self, backend_root: Path) -> None:
        self.backend_root = backend_root.resolve()

    def workspace_root(self, project_id: str, workspace_id: str) -> Path:
        path = (self.backend_root / "workspaces" / project_id / workspace_id).resolve()
        allowed = (self.backend_root / "workspaces" / project_id).resolve()
        if not _is_within(path, allowed):
            raise PermissionError("workspace path escapes the active project")
        return path

    def output_root(self, project_id: str, workspace_id: str) -> Path:
        path = (self.backend_root / "outputs" / project_id / workspace_id).resolve()
        allowed = (self.backend_root / "outputs" / project_id).resolve()
        if not _is_within(path, allowed):
            raise PermissionError("output path escapes the active project")
        return path


def preview_asset_id(source_id: str, page_number: int, kind: str, sha256: str) -> str:
    return _stable_id("asset", source_id, str(page_number), kind, sha256)


def artifact_record(root: Path, path: Path, *, role: str, build_id: str, version: int = 1,
                    page_number: int | None = None) -> dict[str, Any]:
    root = root.resolve()
    path = path.resolve()
    if not path.is_file() or not _is_within(path, root):
        raise ValueError("artifact must be an existing file inside canonical_output_root")
    sha = sha256_file(path)
    relative = path.relative_to(root).as_posix()
    identifier_prefix = "asset" if role == "source_page" else "artifact"
    record = {
        f"{identifier_prefix}_id": _stable_id(identifier_prefix, build_id, role, relative, sha),
        "relative_path": relative,
        "role": role,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size": path.stat().st_size,
        "sha256": sha,
        "version": version,
    }
    if page_number is not None:
        record["page_number"] = page_number
    return record


def build_artifact_manifest(
    *,
    output_root: Path,
    workspace_id: str,
    project_id: str,
    source_id: str,
    job_id: str,
    source_asset_paths: Iterable[tuple[int, Path]],
) -> dict[str, Any]:
    output_root = output_root.resolve()
    files = sorted(path for path in output_root.rglob("*") if path.is_file() and path.name != ARTIFACT_MANIFEST_NAME)
    material = [(path.relative_to(output_root).as_posix(), sha256_file(path)) for path in files]
    build_id = _stable_id("build", workspace_id, job_id, json.dumps(material, ensure_ascii=False))
    source_assets = [
        artifact_record(output_root, path, role="source_page", build_id=build_id, page_number=page)
        for page, path in source_asset_paths
    ]
    known_roles = [
        f"{edition}_{artifact_role}"
        for edition in ("source", "target", "bilingual")
        for artifact_role in ("markdown", "docx", "pdf")
    ]
    role_by_name: dict[str, str] = {}
    build_manifest_path = output_root / "build_manifest.json"
    if build_manifest_path.is_file():
        build_manifest = json.loads(build_manifest_path.read_text("utf-8"))
        for edition, edition_manifest in (build_manifest.get("roles") or {}).items():
            if edition not in {"source", "target", "bilingual"}:
                continue
            for file_format, artifact in (edition_manifest.get("outputs") or {}).items():
                artifact_path = artifact.get("path") if isinstance(artifact, dict) else None
                if not artifact_path or file_format not in {"md", "docx", "pdf"}:
                    continue
                artifact_role = "markdown" if file_format == "md" else file_format
                role_by_name[Path(str(artifact_path)).name] = f"{edition}_{artifact_role}"
    artifacts: list[dict[str, Any]] = []
    by_role: dict[str, dict[str, Any]] = {}
    logs: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    source_paths = {path.resolve() for _, path in source_asset_paths}
    for path in files:
        if path.resolve() in source_paths or path.name == "output_manifest.json":
            continue
        role = role_by_name.get(path.name)
        if role is None:
            role = "log" if "log" in path.parts else "report" if path.suffix.casefold() in {".md", ".csv"} else "support"
        record = artifact_record(output_root, path, role=role, build_id=build_id)
        artifacts.append(record)
        if role in known_roles:
            by_role[role] = record
        elif role == "log":
            logs.append(record)
        elif role == "report":
            reports.append(record)
    manifest: dict[str, Any] = {
        "schema_version": "bookflow-artifact-manifest-v1",
        "workspace_id": workspace_id,
        "project_id": project_id,
        "source_id": source_id,
        "job_id": job_id,
        "build_id": build_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_assets": source_assets,
        "logs": logs,
        "reports": reports,
        "artifacts": artifacts,
        "canonical_output_root": str(output_root),
    }
    for role in known_roles:
        manifest[role] = by_role.get(role)
    atomic_write_json(output_root / ARTIFACT_MANIFEST_NAME, manifest)
    return manifest


class ArtifactResolver:
    """Resolve IDs only from the manifest owned by the active project/job."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.root = Path(str(manifest["canonical_output_root"])).resolve()

    def _records(self) -> list[dict[str, Any]]:
        return [*self.manifest.get("source_assets", []), *self.manifest.get("artifacts", [])]

    def resolve(self, identifier: str) -> ResolvedItem:
        record = next(
            (item for item in self._records() if item.get("asset_id") == identifier or item.get("artifact_id") == identifier),
            None,
        )
        if record is None:
            raise KeyError("asset or artifact is not present in the active manifest")
        path = (self.root / str(record["relative_path"])).resolve()
        if not path.is_file() or not _is_within(path, self.root):
            raise FileNotFoundError("manifest target is missing or outside canonical_output_root")
        actual_sha = sha256_file(path)
        if actual_sha != record["sha256"]:
            raise ValueError("artifact hash does not match its manifest")
        return ResolvedItem(
            identifier=identifier,
            path=path,
            role=str(record["role"]),
            mime_type=str(record["mime_type"]),
            sha256=actual_sha,
            build_id=str(self.manifest["build_id"]),
        )
