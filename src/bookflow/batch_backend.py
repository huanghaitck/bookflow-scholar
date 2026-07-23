"""Durable multi-book backend used by the desktop Bridge contract.

The module deliberately keeps UI concerns out of the backend. File pickers and
drag/drop both pass paths to :meth:`BatchBackend.import_sources`.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import fitz
import yaml

from .artifact_resolver import ArtifactResolver, build_artifact_manifest, preview_asset_id
from .io_utils import atomic_write_json
from .multilingual_workspace import (
    SUPPORTED_LANGUAGES,
    build_workspace,
    create_workspace,
    detect_language,
    inspect_workspace,
    plan_workspace,
    render_workspace,
    translate_workspace,
)
from .providers.mock import MockTranslationProvider
from .provider_registry import ProviderRegistry, parse_model_json
from .production_pipeline import PipelineControlRequested, ProductionPipeline
from .web_assist import WebAssistExportRequest, WebAssistImportRequest, WebAssistService


SCHEMA_VERSION = 3
CONTRACT_VERSION = "1.2.0"
PIPELINE_CONFIG_VERSION = "s9-r3-v1"
SUPPORTED_SOURCE_FORMATS = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}
REDACT = re.compile(r"(?i)(api[_-]?key|authorization|bearer|secret|token)(\s*[:=]\s*)\S+")
BEARER = re.compile(r"(?i)bearer\s+\S+")


@dataclass
class WorkerSession:
    session_id: str
    project_id: str
    batch_id: str
    thread: threading.Thread
    started_at: str


class WorkerRegistry:
    """Own the live worker identity for every batch in the persistent sidecar."""

    def __init__(self) -> None:
        self._sessions: dict[str, WorkerSession] = {}
        self._lock = threading.RLock()

    def get(self, batch_id: str) -> WorkerSession | None:
        with self._lock:
            session = self._sessions.get(batch_id)
            return session if session and session.thread.is_alive() else None

    def register(self, session: WorkerSession) -> None:
        with self._lock:
            self._sessions[session.batch_id] = session

    def finish(self, batch_id: str, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(batch_id)
            if session and session.session_id == session_id:
                self._sessions.pop(batch_id, None)

    def live(self) -> list[WorkerSession]:
        with self._lock:
            return [session for session in self._sessions.values() if session.thread.is_alive()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    clean = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-._")
    return clean[:80] or "book"


def error_envelope(exc: Exception, *, code: str = "backend_error", recoverable: bool = True,
                   stage: str | None = None, job_id: str | None = None,
                   source_id: str | None = None) -> dict[str, Any]:
    message = BEARER.sub("Bearer [REDACTED]", str(exc))
    message = REDACT.sub(r"\1\2[REDACTED]", message)
    return {
        "schema_version": "bookflow-error-v1.2", "error_code": code,
        "severity": "error", "user_message": message[:500],
        "technical_message": message[:1000], "retryable": recoverable,
        "stage": stage, "job_id": job_id, "source_id": source_id,
        "timestamp": _now(), "details": {},
        "code": code,
        "message": message[:1000],
        "recoverable": recoverable,
        "technical_type": type(exc).__name__,
    }


class BatchBackend:
    """SQLite-backed project/import/batch service with a sequential worker."""

    def __init__(self, root: Path, *, provider_config_path: Path | None = None, real_call_budget: Any = None,
                 background_worker: bool = False) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "bookflow_backend.sqlite3"
        self.provider_config_path = provider_config_path.resolve() if provider_config_path else None
        self.real_call_budget = real_call_budget
        self.background_worker = background_worker
        self.production_pipeline = ProductionPipeline(self.root, provider_config_path=self.provider_config_path)
        self.worker_registry = WorkerRegistry()
        self._shutdown_requested = threading.Event()
        self._lock = threading.RLock()
        self._migrate()
        self._migrate_legacy_scoped_paths()
        self.web_assist = WebAssistService(self.root)
        self._recover_interrupted_jobs()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _migrate(self) -> None:
        migrations = [
            """
            CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL);
            INSERT INTO schema_meta(version)
                SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);
            """,
            """
            CREATE TABLE IF NOT EXISTS projects(
                project_id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources(
                source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
                source_path TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, page_count INTEGER NOT NULL,
                source_language TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batches(
                batch_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
                state TEXT NOT NULL, pause_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs(
                job_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(batch_id),
                source_id TEXT NOT NULL REFERENCES sources(source_id), state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, progress REAL NOT NULL DEFAULT 0,
                output_path TEXT, error_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(batch_id, source_id)
            );
            CREATE TABLE IF NOT EXISTS commands(
                command_id TEXT PRIMARY KEY, command_name TEXT NOT NULL,
                response_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL, project_id TEXT, batch_id TEXT, job_id TEXT,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_batch_state ON jobs(batch_id, state);
            CREATE INDEX IF NOT EXISTS idx_events_batch_sequence ON events(batch_id, sequence);
            """,
            """
            CREATE TABLE IF NOT EXISTS project_sources(
                project_id TEXT NOT NULL REFERENCES projects(project_id),
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                linked_at TEXT NOT NULL, PRIMARY KEY(project_id,source_id)
            );
            INSERT OR IGNORE INTO project_sources(project_id,source_id,linked_at)
                SELECT project_id,source_id,created_at FROM sources;
            ALTER TABLE projects ADD COLUMN active_source_id TEXT;
            ALTER TABLE batches ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}';
            ALTER TABLE jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued';
            ALTER TABLE jobs ADD COLUMN provider_id TEXT;
            ALTER TABLE jobs ADD COLUMN model_alias TEXT;
            ALTER TABLE jobs ADD COLUMN request_count INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE jobs ADD COLUMN latency_seconds REAL NOT NULL DEFAULT 0;
            ALTER TABLE jobs ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}';
            CREATE TABLE IF NOT EXISTS warning_acknowledgements(
                warning_id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(project_id),
                acknowledged_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS active_context(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                active_project_id TEXT REFERENCES projects(project_id),
                active_source_id TEXT REFERENCES sources(source_id),
                active_batch_id TEXT REFERENCES batches(batch_id),
                active_job_id TEXT REFERENCES jobs(job_id),
                workspace_id TEXT,
                source_language TEXT,
                target_language TEXT,
                provider_profile_id TEXT,
                pipeline_config_version TEXT NOT NULL DEFAULT 's9-r3-v1',
                updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO active_context(singleton,pipeline_config_version,updated_at)
                VALUES(1,'s9-r3-v1',CURRENT_TIMESTAMP);
            ALTER TABLE project_sources ADD COLUMN source_language TEXT;
            ALTER TABLE jobs ADD COLUMN workspace_id TEXT;
            ALTER TABLE jobs ADD COLUMN worker_session_id TEXT;
            UPDATE project_sources
               SET source_language=(SELECT sources.source_language FROM sources
                                    WHERE sources.source_id=project_sources.source_id)
             WHERE source_language IS NULL;
            """,
        ]
        with self._connect() as connection:
            current = int(connection.execute("SELECT version FROM schema_meta").fetchone()[0]) if self._has_meta(connection) else 0
            if current < 1:
                connection.executescript(migrations[0])
                connection.executescript(migrations[1])
                connection.execute("UPDATE schema_meta SET version=1")
                current = 1
            if current < 2:
                connection.executescript(migrations[2])
                connection.execute("UPDATE schema_meta SET version=2")
                current = 2
            if current < 3:
                connection.executescript(migrations[3])
                connection.execute("UPDATE schema_meta SET version=3")
                current = 3
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"database schema {current} is newer than supported {SCHEMA_VERSION}")

    @staticmethod
    def _has_meta(connection: sqlite3.Connection) -> bool:
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'").fetchone() is not None

    def _set_active_context(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        source_id: str | None = None,
        batch_id: str | None = None,
        job_id: str | None = None,
        workspace_id: str | None = None,
        source_language: str | None = None,
        target_language: str | None = None,
        provider_profile_id: str | None = None,
    ) -> None:
        connection.execute(
            """UPDATE active_context
                  SET active_project_id=?,active_source_id=?,active_batch_id=?,active_job_id=?,
                      workspace_id=?,source_language=?,target_language=?,provider_profile_id=?,
                      pipeline_config_version=?,updated_at=?
                WHERE singleton=1""",
            (project_id, source_id, batch_id, job_id, workspace_id, source_language,
             target_language, provider_profile_id, PIPELINE_CONFIG_VERSION, _now()),
        )

    def _active_context(self, connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM active_context WHERE singleton=1").fetchone()
        return dict(row) if row else {}

    def _provider_config_hash(self) -> str:
        if self.provider_config_path and self.provider_config_path.is_file():
            return _sha256(self.provider_config_path)
        return "unconfigured"

    def _workspace_identity(self, project_id: str, source_id: str, config: dict[str, Any]) -> str:
        material = {
            "project_id": project_id,
            "source_id": source_id,
            "source_language": config["source_language"],
            "target_language": config["target_language"],
            "vision_provider_id": config["vision_provider_id"],
            "text_provider_id": config["text_provider_id"],
            "provider_config_hash": self._provider_config_hash(),
            "pipeline_config_version": PIPELINE_CONFIG_VERSION,
            "ocr_mode": config["ocr_mode"],
            "translation_enabled": config["translation_enabled"],
            "structure_enabled": config["structure_enabled"],
            "output_formats": config["output_formats"],
        }
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return f"workspace_{digest[:24]}"

    def _activate_project_source(
        self, connection: sqlite3.Connection, *, project_id: str, source_id: str | None
    ) -> None:
        """Restore active job/workspace state only from the selected project's durable records."""
        if not source_id:
            self._set_active_context(connection, project_id=project_id)
            return
        row = connection.execute(
            """SELECT j.job_id,j.batch_id,j.workspace_id,b.config_json,
                      COALESCE(ps.source_language,s.source_language) AS source_language
                 FROM project_sources ps
                 JOIN sources s USING(source_id)
            LEFT JOIN batches b ON b.project_id=ps.project_id
            LEFT JOIN jobs j ON j.batch_id=b.batch_id AND j.source_id=ps.source_id
                WHERE ps.project_id=? AND ps.source_id=?
             ORDER BY j.updated_at DESC,j.created_at DESC LIMIT 1""",
            (project_id, source_id),
        ).fetchone()
        if row is None:
            raise KeyError("source is not linked to project")
        config = json.loads(row["config_json"]) if row["config_json"] else {}
        self._set_active_context(
            connection,
            project_id=project_id,
            source_id=source_id,
            batch_id=row["batch_id"],
            job_id=row["job_id"],
            workspace_id=row["workspace_id"],
            source_language=row["source_language"] or config.get("source_language"),
            target_language=config.get("target_language"),
            provider_profile_id=(
                f"{config.get('text_provider_id')}|{config.get('vision_provider_id')}" if config else None
            ),
        )

    def _recover_interrupted_jobs(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id,batch_id FROM jobs WHERE state IN ('running','pausing','retrying','recovering')"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE jobs SET state='queued',stage='recovering',worker_session_id=NULL,updated_at=? WHERE job_id=?",
                    (_now(), row["job_id"]),
                )
                self._event(connection, "pipeline.recovering", batch_id=row["batch_id"], job_id=row["job_id"], payload={"reason": "worker_restart"})
            connection.execute(
                "UPDATE batches SET state='queued',pause_requested=0,updated_at=? WHERE state IN ('running','cancelling')",
                (_now(),),
            )

    def _migrate_legacy_scoped_paths(self) -> None:
        """Copy legacy global workspaces/outputs into their computed project scope without provider calls."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT j.job_id,j.source_id,j.workspace_id,j.output_path,j.state,
                          b.project_id,b.config_json,s.source_path,s.page_count
                     FROM jobs j JOIN batches b USING(batch_id) JOIN sources s USING(source_id)"""
            ).fetchall()
        for raw in rows:
            row = dict(raw); config = json.loads(row["config_json"])
            workspace_id = row.get("workspace_id") or self._workspace_identity(row["project_id"], row["source_id"], config)
            if not row.get("workspace_id"):
                with self._connect() as connection:
                    connection.execute("UPDATE jobs SET workspace_id=? WHERE job_id=?", (workspace_id, row["job_id"]))
            scoped_workspace = self.root / "workspaces" / row["project_id"] / workspace_id
            legacy_workspace = self.root / "workspaces" / row["source_id"]
            if legacy_workspace.is_dir() and not scoped_workspace.exists():
                shutil.copytree(legacy_workspace, scoped_workspace)
            output = Path(row["output_path"]).resolve() if row.get("output_path") else None
            scoped_output = (self.root / "outputs" / row["project_id"] / workspace_id).resolve()
            if output and output.is_dir() and output != scoped_output:
                if not scoped_output.exists():
                    shutil.copytree(output, scoped_output)
                output = scoped_output
                with self._connect() as connection:
                    connection.execute("UPDATE jobs SET output_path=?,updated_at=? WHERE job_id=?",
                                       (str(output), _now(), row["job_id"]))
            if row["state"] != "completed" or output is None or not output.is_dir():
                continue
            self._generate_source_previews(row["source_id"], Path(row["source_path"]), int(row["page_count"]))
            artifact_manifest = output / "artifact_manifest.json"
            if artifact_manifest.is_file():
                continue
            preview = json.loads((self.root / "previews" / row["source_id"] / "preview_manifest.json").read_text("utf-8"))
            asset_root = output / "assets/source"; asset_root.mkdir(parents=True, exist_ok=True)
            assets: list[tuple[int, Path]] = []
            for item in preview.get("thumbnails", []):
                source_asset = Path(item["path"])
                destination = asset_root / f"page-{int(item['page']):04d}{source_asset.suffix.lower()}"
                shutil.copy2(source_asset, destination); assets.append((int(item["page"]), destination))
            build_artifact_manifest(
                output_root=output, workspace_id=workspace_id, project_id=row["project_id"],
                source_id=row["source_id"], job_id=row["job_id"], source_asset_paths=assets,
            )

    def create_project(self, name: str, *, project_id: str | None = None) -> dict[str, Any]:
        project_id = project_id or _id("project")
        project_root = self.root / "projects" / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects(project_id,name,root_path,state,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (project_id, name, str(project_root), "open", now, now),
            )
            self._set_active_context(connection, project_id=project_id)
            self._event(connection, "project.loaded", project_id=project_id, payload={"name": name})
        return {"project_id": project_id, "name": name, "root_path": str(project_root), "state": "open"}

    def close_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            changed = connection.execute("UPDATE projects SET state='closed',updated_at=? WHERE project_id=?", (_now(), project_id)).rowcount
            if not changed:
                raise KeyError(f"project not found: {project_id}")
            self._event(connection, "project.closed", project_id=project_id, payload={})
        return {"project_id": project_id, "state": "closed"}

    def open_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
            if row is None:
                raise KeyError(f"project not found: {project_id}")
            connection.execute("UPDATE projects SET state='open',updated_at=? WHERE project_id=?", (_now(), project_id))
            self._activate_project_source(connection, project_id=project_id, source_id=row["active_source_id"])
            self._event(connection, "project.loaded", project_id=project_id, payload={"reopened": row["state"] == "closed"})
        return {"project_id": project_id, "name": row["name"], "root_path": row["root_path"], "state": "open"}

    def _discover(self, paths: Iterable[Path], recursive: bool) -> list[Path]:
        found: list[Path] = []
        for supplied in paths:
            path = supplied.expanduser().resolve()
            if not path.exists():
                found.append(path)
            elif path.is_dir():
                iterator = path.rglob("*") if recursive else path.glob("*")
                found.extend(sorted(item.resolve() for item in iterator if item.is_file()))
            else:
                found.append(path)
        unique: dict[str, Path] = {}
        for path in found:
            unique.setdefault(str(path).casefold(), path)
        return list(unique.values())

    def import_sources(
        self,
        project_id: str | None,
        paths: Iterable[Path],
        *,
        command_id: str,
        recursive: bool = True,
        source_languages: dict[str, str] | None = None,
        pipeline_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Unified file/folder import used by pickers and drag/drop."""
        cached = self._command(command_id)
        if cached is not None:
            return cached
        supplied_paths = tuple(paths)
        if not project_id:
            seed = supplied_paths[0] if supplied_paths else None
            inferred_name = (seed.stem if seed and seed.suffix else seed.name if seed else "")
            project_id = self.create_project(inferred_name.strip() or "Untitled Book")["project_id"]
        candidates = self._discover(supplied_paths, recursive)
        results: list[dict[str, Any]] = []
        created_source_ids: list[str] = []
        with self._connect() as connection:
            project = connection.execute("SELECT state FROM projects WHERE project_id=?", (project_id,)).fetchone()
            if project is None:
                raise KeyError(f"project not found: {project_id}")
            if project["state"] != "open":
                raise PermissionError("project is closed; openProject is required before import")
            self._event(connection, "import.started", project_id=project_id, payload={"candidate_count": len(candidates)})
            for path in candidates:
                try:
                    result = self._import_one(connection, project_id, path, source_languages or {})
                    if result.get("result_code") in {"accepted", "reused_immutable_source"}:
                        created_source_ids.append(result["source_id"])
                except Exception as exc:
                    result_code = "unreadable" if isinstance(exc, (FileNotFoundError, OSError, ValueError)) else "failed"
                    result = {"path": str(path), "status": "failed", "result_code": result_code,
                              "error": error_envelope(exc, code="import_failed")}
                results.append(result)
                self._event(connection, "import.progress", project_id=project_id, payload={"completed": len(results), "total": len(candidates), "result": result})
            batch_id = _id("batch")
            now = _now()
            config = self._validate_pipeline_config(pipeline_config or {})
            connection.execute("INSERT INTO batches(batch_id,project_id,state,created_at,updated_at,config_json) VALUES(?,?,?,?,?,?)", (batch_id, project_id, "queued", now, now, json.dumps(config, ensure_ascii=False)))
            active_job_id: str | None = None
            active_workspace_id: str | None = None
            for source_id in created_source_ids:
                job_id = _id("job")
                workspace_id = self._workspace_identity(project_id, source_id, config)
                connection.execute(
                    "INSERT INTO jobs(job_id,batch_id,source_id,state,created_at,updated_at,workspace_id) VALUES(?,?,?,?,?,?,?)",
                    (job_id, batch_id, source_id, "queued", now, now, workspace_id),
                )
                active_job_id, active_workspace_id = job_id, workspace_id
            if created_source_ids:
                active_source_id = created_source_ids[-1]
                connection.execute(
                    "UPDATE projects SET active_source_id=?,updated_at=? WHERE project_id=?",
                    (active_source_id, now, project_id),
                )
                self._set_active_context(
                    connection,
                    project_id=project_id,
                    source_id=active_source_id,
                    batch_id=batch_id,
                    job_id=active_job_id,
                    workspace_id=active_workspace_id,
                    source_language=config["source_language"],
                    target_language=config["target_language"],
                    provider_profile_id=f"{config['text_provider_id']}|{config['vision_provider_id']}",
                )
            summary = {
                "project_id": project_id,
                "batch_id": batch_id,
                "discovered": len(candidates),
                "imported": sum(item.get("result_code") == "accepted" for item in results),
                "linked": sum(item.get("result_code") == "reused_immutable_source" for item in results),
                "duplicates": sum(item.get("result_code") == "duplicate_in_project" for item in results),
                "unsupported": sum(item.get("result_code") == "unsupported" for item in results),
                "unreadable": sum(item.get("result_code") == "unreadable" for item in results),
                "failed": sum(item["status"] == "failed" for item in results),
                "results": results,
            }
            event_name = "import.completed" if summary["imported"] or summary["linked"] or summary["duplicates"] else "import.failed"
            self._event(connection, event_name, project_id=project_id, batch_id=batch_id, payload={key: summary[key] for key in ("discovered", "imported", "duplicates", "failed")})
            connection.execute("INSERT INTO commands(command_id,command_name,response_json,created_at) VALUES(?,?,?,?)", (command_id, "importSources", json.dumps(summary, ensure_ascii=False), now))
        return summary

    def _import_one(self, connection: sqlite3.Connection, project_id: str, path: Path, languages: dict[str, str]) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        extension = path.suffix.lower()
        if extension not in SUPPORTED_SOURCE_FORMATS:
            return {"path": str(path), "status": "failed", "result_code": "unsupported",
                    "reason": f"unsupported source format: {extension or '<none>'}"}
        sha = _sha256(path)
        existing = connection.execute("SELECT source_id FROM sources WHERE sha256=?", (sha,)).fetchone()
        if existing:
            associated = connection.execute("SELECT 1 FROM project_sources WHERE project_id=? AND source_id=?", (project_id, existing["source_id"])).fetchone()
            if associated:
                return {"path": str(path), "status": "duplicate", "result_code": "duplicate_in_project",
                        "source_id": existing["source_id"], "sha256": sha}
            language = languages.get(str(path)) or languages.get(path.name)
            connection.execute(
                "INSERT INTO project_sources(project_id,source_id,linked_at,source_language) VALUES(?,?,?,?)",
                (project_id, existing["source_id"], _now(), language),
            )
            return {"path": str(path), "status": "linked", "result_code": "reused_immutable_source",
                    "source_id": existing["source_id"], "sha256": sha}
        if extension == ".pdf":
            document = fitz.open(path)
            try:
                if document.needs_pass: raise ValueError("encrypted PDF requires a password")
                if len(document) < 1: raise ValueError("PDF contains no pages")
                page_count = len(document)
                text_sample = "\n".join(document[index].get_text("text") for index in range(min(5, page_count)))
            finally: document.close()
        else:
            from PIL import Image
            with Image.open(path) as image: image.verify()
            page_count, text_sample = 1, ""
        language = languages.get(str(path)) or languages.get(path.name) or detect_language(text_sample)
        if language != "auto" and language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language: {language}")
        source_id = f"source_{sha[:24]}"
        connection.execute(
            "INSERT INTO sources(source_id,project_id,source_path,filename,sha256,mime_type,size_bytes,page_count,source_language,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, project_id, str(path), path.name, sha, SUPPORTED_SOURCE_FORMATS[extension], path.stat().st_size, page_count, language, "ready", _now()),
        )
        connection.execute(
            "INSERT INTO project_sources(project_id,source_id,linked_at,source_language) VALUES(?,?,?,?)",
            (project_id, source_id, _now(), language),
        )
        self._generate_source_previews(source_id, path, page_count)
        return {"path": str(path), "status": "imported", "result_code": "accepted", "source_id": source_id,
                "sha256": sha, "page_count": page_count, "language": language}

    def _generate_source_previews(self, source_id: str, path: Path, page_count: int) -> None:
        preview_dir = self.root / "previews" / source_id
        manifest_path = preview_dir / "preview_manifest.json"
        if manifest_path.is_file():
            try:
                existing = json.loads(manifest_path.read_text("utf-8"))
                if existing.get("schema_version") == "bookflow-source-assets-v2":
                    return
            except (OSError, json.JSONDecodeError):
                pass
        preview_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        if path.suffix.casefold() == ".pdf":
            document = fitz.open(path)
            try:
                for index, page in enumerate(document, 1):
                    image = preview_dir / "pages" / f"page-{index:04d}.png"
                    thumbnail = preview_dir / "thumbnails" / f"page-{index:04d}.png"
                    image.parent.mkdir(parents=True, exist_ok=True)
                    thumbnail.parent.mkdir(parents=True, exist_ok=True)
                    page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False).save(image)
                    page.get_pixmap(matrix=fitz.Matrix(0.28, 0.28), alpha=False).save(thumbnail)
                    image_sha = _sha256(image); thumbnail_sha = _sha256(thumbnail)
                    records.append({
                        "page": index,
                        "asset_id": preview_asset_id(source_id, index, "page", image_sha),
                        "thumbnail_asset_id": preview_asset_id(source_id, index, "thumbnail", thumbnail_sha),
                        "path": str(image), "thumbnail_path": str(thumbnail),
                        "sha256": image_sha, "thumbnail_sha256": thumbnail_sha,
                    })
            finally:
                document.close()
        else:
            from PIL import Image
            image = preview_dir / "pages" / "page-0001.png"
            thumbnail = preview_dir / "thumbnails" / "page-0001.png"
            image.parent.mkdir(parents=True, exist_ok=True); thumbnail.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(path) as source_image:
                source_image.convert("RGB").save(image)
                small = source_image.copy(); small.thumbnail((240, 320)); small.convert("RGB").save(thumbnail)
            image_sha = _sha256(image); thumbnail_sha = _sha256(thumbnail)
            records.append({
                "page": 1,
                "asset_id": preview_asset_id(source_id, 1, "page", image_sha),
                "thumbnail_asset_id": preview_asset_id(source_id, 1, "thumbnail", thumbnail_sha),
                "path": str(image), "thumbnail_path": str(thumbnail),
                "sha256": image_sha, "thumbnail_sha256": thumbnail_sha,
            })
        manifest_path.write_text(json.dumps({
            "schema_version": "bookflow-source-assets-v2",
            "source_id": source_id, "page_count": page_count,
            "cover_asset_id": records[0]["thumbnail_asset_id"] if records else None,
            "thumbnails": records,
        }, ensure_ascii=False, indent=2) + "\n", "utf-8")

    def _validate_pipeline_config(self, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {"vision_provider_id", "text_provider_id", "source_language", "target_language", "ocr_mode", "translation_enabled", "structure_enabled", "output_formats"}
        unknown = set(value) - allowed
        if unknown: raise ValueError(f"unknown pipeline config fields: {sorted(unknown)}")
        default_vision = "mock"; default_text = "mock"
        if self.provider_config_path is not None:
            registry = ProviderRegistry.load(self.provider_config_path)
            default_vision = registry.active_vision or default_vision
            default_text = registry.active_text or default_text
        result = {"vision_provider_id": value.get("vision_provider_id", default_vision), "text_provider_id": value.get("text_provider_id", default_text), "source_language": value.get("source_language", "auto"), "target_language": value.get("target_language", "zh-Hans"), "ocr_mode": value.get("ocr_mode", "auto"), "translation_enabled": bool(value.get("translation_enabled", True)), "structure_enabled": bool(value.get("structure_enabled", True)), "output_formats": list(value.get("output_formats", ["md", "docx", "pdf"]))}
        if result["source_language"] != "auto" and result["source_language"] not in SUPPORTED_LANGUAGES: raise ValueError("unsupported source_language")
        if result["target_language"] not in SUPPORTED_LANGUAGES: raise ValueError("unsupported target_language")
        return result

    def run_batch(self, batch_id: str, *, processor: Callable[[dict[str, Any]], dict[str, Any]] | None = None, max_jobs: int | None = None) -> dict[str, Any]:
        processor = processor or self._process_job
        processed = 0
        with self._connect() as connection:
            batch = connection.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise KeyError(f"batch not found: {batch_id}")
            connection.execute("UPDATE batches SET state='running',updated_at=? WHERE batch_id=?", (_now(), batch_id))
            self._event(connection, "pipeline.queued", project_id=batch["project_id"], batch_id=batch_id, payload={})
        while max_jobs is None or processed < max_jobs:
            with self._connect() as connection:
                batch = connection.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
                if batch["pause_requested"]:
                    connection.execute("UPDATE batches SET state='paused',updated_at=? WHERE batch_id=?", (_now(), batch_id))
                    self._event(connection, "pipeline.paused", batch_id=batch_id, payload={})
                    break
                job = connection.execute(
                    """SELECT j.*,s.source_path,s.filename,s.sha256,s.mime_type,s.size_bytes,s.page_count,
                              COALESCE(ps.source_language,s.source_language) AS source_language,
                              b.config_json,b.project_id
                         FROM jobs j
                         JOIN sources s USING(source_id)
                         JOIN batches b USING(batch_id)
                         LEFT JOIN project_sources ps ON ps.project_id=b.project_id AND ps.source_id=j.source_id
                        WHERE j.batch_id=? AND j.state='queued'
                        ORDER BY j.created_at,j.job_id LIMIT 1""",
                    (batch_id,),
                ).fetchone()
                if job is None:
                    break
                job_data = dict(job)
                session = self.worker_registry.get(batch_id)
                connection.execute(
                    "UPDATE jobs SET state='running',stage='processing',attempts=attempts+1,progress=0.05,worker_session_id=?,updated_at=? WHERE job_id=?",
                    (session.session_id if session else None, _now(), job["job_id"]),
                )
                self._set_active_context(
                    connection, project_id=job["project_id"], source_id=job["source_id"], batch_id=batch_id,
                    job_id=job["job_id"], workspace_id=job["workspace_id"],
                    source_language=json.loads(job["config_json"])["source_language"],
                    target_language=json.loads(job["config_json"])["target_language"],
                )
                self._event(connection, "pipeline.stage_started", batch_id=batch_id, job_id=job["job_id"], payload={"stage": "process"})
            try:
                result = processor(job_data)
                with self._connect() as connection:
                    current = connection.execute("SELECT state FROM jobs WHERE job_id=?", (job_data["job_id"],)).fetchone()["state"]
                    if current not in {"cancelled", "cancelling", "pausing"}:
                        connection.execute("UPDATE jobs SET state='completed',stage='completed',progress=1,output_path=?,provider_id=?,model_alias=?,request_count=?,retry_count=?,latency_seconds=?,usage_json=?,error_json=NULL,updated_at=? WHERE job_id=?", (result.get("output_path"), result.get("provider_id") or result.get("provider"), result.get("model_alias"), int(result.get("request_count", 0)), int(result.get("retry_count", 0)), float(result.get("latency_seconds", 0)), json.dumps(result.get("usage", {}), ensure_ascii=False), _now(), job_data["job_id"]))
                        self._event(connection, "pipeline.stage_completed", batch_id=batch_id, job_id=job_data["job_id"], payload=result)
                    elif current == "pausing":
                        connection.execute(
                            "UPDATE jobs SET state='paused',stage='paused',worker_session_id=NULL,updated_at=? WHERE job_id=?",
                            (_now(), job_data["job_id"]),
                        )
                        connection.execute("UPDATE batches SET state='paused',updated_at=? WHERE batch_id=?", (_now(), batch_id))
                        self._event(connection, "pipeline.paused", batch_id=batch_id, job_id=job_data["job_id"], payload={"checkpoint_preserved": True})
            except PipelineControlRequested as exc:
                with self._connect() as connection:
                    if exc.action == "pause":
                        connection.execute("UPDATE jobs SET state='paused',stage='paused',worker_session_id=NULL,updated_at=? WHERE job_id=? AND state NOT IN ('cancelled','cancelling')", (_now(), job_data["job_id"]))
                        connection.execute("UPDATE batches SET state='paused',updated_at=? WHERE batch_id=?", (_now(), batch_id))
                        self._event(connection, "pipeline.paused", batch_id=batch_id, job_id=job_data["job_id"], payload={"checkpoint_preserved": True})
                    elif exc.action == "cancel":
                        connection.execute("UPDATE jobs SET state='cancelled',stage='cancelled',worker_session_id=NULL,updated_at=? WHERE job_id=?", (_now(), job_data["job_id"]))
                        self._event(connection, "pipeline.cancelled", batch_id=batch_id, job_id=job_data["job_id"], payload={"checkpoint_preserved": True})
                break
            except Exception as exc:
                error_code = "provider_schema_error" if type(exc).__name__ == "ProviderSchemaError" else "job_failed"
                envelope = error_envelope(exc, code=error_code, stage="processing", job_id=job_data["job_id"], source_id=job_data["source_id"])
                with self._connect() as connection:
                    connection.execute("UPDATE jobs SET state='failed',stage='failed',worker_session_id=NULL,error_json=?,updated_at=? WHERE job_id=?", (json.dumps(envelope, ensure_ascii=False), _now(), job_data["job_id"]))
                    self._event(connection, "pipeline.failed", batch_id=batch_id, job_id=job_data["job_id"], payload=envelope)
            processed += 1
        return self._finalize_batch(batch_id)

    def _process_job(self, job: dict[str, Any]) -> dict[str, Any]:
        def progress(stage: str, value: float, details: dict[str, Any]) -> None:
            with self._connect() as connection:
                current = connection.execute("SELECT state FROM jobs WHERE job_id=?", (job["job_id"],)).fetchone()
                if current is None or current["state"] == "cancelled":
                    return
                connection.execute("UPDATE jobs SET stage=?,progress=?,updated_at=? WHERE job_id=?",
                                   (stage, max(0.0, min(1.0, value)), _now(), job["job_id"]))
                self._event(connection, "pipeline.progress", batch_id=job["batch_id"], job_id=job["job_id"],
                            payload={"stage": stage, "progress": value, **details})

        def stop() -> str | None:
            if self._shutdown_requested.is_set():
                return "pause"
            with self._connect() as connection:
                state = connection.execute("SELECT state FROM jobs WHERE job_id=?", (job["job_id"],)).fetchone()
                batch = connection.execute("SELECT pause_requested FROM batches WHERE batch_id=?", (job["batch_id"],)).fetchone()
            if state and state["state"] in {"cancelled", "cancelling"}:
                return "cancel"
            if batch and batch["pause_requested"]:
                return "pause"
            return None

        return self.production_pipeline.run(job, progress=progress, stop=stop)

    def start_batch(self, batch_id: str) -> dict[str, Any]:
        """Acknowledge immediately and let the persistent sidecar worker run."""
        with self._lock:
            existing = self.worker_registry.get(batch_id)
            if existing:
                return {"batch_id": batch_id, "state": "running", "worker": "existing",
                        "worker_session_id": existing.session_id, "accepted_async": True}
            with self._connect() as connection:
                batch = connection.execute("SELECT project_id,state FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
                if batch is None:
                    raise KeyError(f"batch not found: {batch_id}")
                pending = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE batch_id=? AND state IN ('queued','paused','retrying','recovering')",
                    (batch_id,),
                ).fetchone()[0]
                if not pending:
                    return {"batch_id": batch_id, "state": batch["state"], "worker": "not_needed",
                            "worker_session_id": None, "accepted_async": False}
                connection.execute("UPDATE batches SET state='queued',pause_requested=0,updated_at=? WHERE batch_id=?", (_now(), batch_id))
            self._shutdown_requested.clear()
            session_id = _id("worker")
            def run_owned() -> None:
                try:
                    self.run_batch(batch_id)
                finally:
                    self.worker_registry.finish(batch_id, session_id)
            thread = threading.Thread(target=run_owned, name=f"bookflow-{batch_id}", daemon=True)
            session = WorkerSession(session_id, batch["project_id"], batch_id, thread, _now())
            self.worker_registry.register(session)
            thread.start()
        return {"batch_id": batch_id, "state": "queued", "worker": "started",
                "worker_session_id": session_id, "accepted_async": True}

    def shutdown(self, *, timeout: float = 10.0) -> dict[str, Any]:
        self._shutdown_requested.set()
        workers = self.worker_registry.live()
        deadline = time.monotonic() + timeout
        for session in workers:
            session.thread.join(max(0.0, deadline - time.monotonic()))
        alive = self.worker_registry.live()
        return {"stopped": not alive, "worker_alive": bool(alive),
                "worker_sessions": [session.session_id for session in alive]}

    def _finalize_batch(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT state,COUNT(*) AS count FROM jobs WHERE batch_id=? GROUP BY state", (batch_id,)).fetchall()
            counts = {row["state"]: row["count"] for row in rows}
            pending = sum(counts.get(name, 0) for name in ("queued", "running", "pausing", "retrying", "recovering", "cancelling"))
            batch = connection.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch["pause_requested"] or counts.get("paused"):
                state = "paused"
            elif pending:
                state = "queued"
            elif counts.get("failed"):
                state = "failed"
            elif counts.get("cancelled") and not counts.get("completed"):
                state = "cancelled"
            else:
                state = "completed"
            connection.execute("UPDATE batches SET state=?,updated_at=? WHERE batch_id=?", (state, _now(), batch_id))
            if state == "completed":
                self._event(connection, "pipeline.completed", batch_id=batch_id, payload={"counts": counts})
            elif state == "failed":
                self._event(connection, "pipeline.failed", batch_id=batch_id, payload={"counts": counts})
        return {"batch_id": batch_id, "state": state, "counts": counts, "total": sum(counts.values())}

    def pause_batch(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError(f"batch not found: {batch_id}")
            connection.execute("UPDATE batches SET state='paused',pause_requested=1,updated_at=? WHERE batch_id=?", (_now(), batch_id))
            connection.execute("UPDATE jobs SET state='pausing',stage='pausing',updated_at=? WHERE batch_id=? AND state='running'", (_now(), batch_id))
            self._event(connection, "pipeline.warning", batch_id=batch_id, payload={"message": "pause requested; current stage stops at its cancellation boundary"})
        return {"batch_id": batch_id, "pause_requested": True}

    def resume_batch(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError(f"batch not found: {batch_id}")
            connection.execute("UPDATE jobs SET state='queued',stage='recovering',updated_at=? WHERE batch_id=? AND state IN ('paused','pausing','recovering')", (_now(), batch_id))
            connection.execute("UPDATE batches SET pause_requested=0,state='queued',updated_at=? WHERE batch_id=?", (_now(), batch_id))
            self._event(connection, "pipeline.resumed", project_id=row["project_id"], batch_id=batch_id, payload={})
        worker = self.start_batch(batch_id) if self.background_worker else {"worker": "manual", "worker_session_id": None}
        return {"batch_id": batch_id, "state": "queued", **worker}

    def recover_batch(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError(f"batch not found: {batch_id}")
            connection.execute(
                "UPDATE jobs SET state='queued',stage='recovering',updated_at=? WHERE batch_id=? AND state IN ('paused','pausing','recovering','queued')",
                (_now(), batch_id),
            )
            connection.execute("UPDATE batches SET pause_requested=0,state='queued',updated_at=? WHERE batch_id=?", (_now(), batch_id))
            self._event(connection, "pipeline.recovering", project_id=row["project_id"], batch_id=batch_id,
                        payload={"reason": "checkpoint", "worker_restart_required": True})
        worker = self.start_batch(batch_id) if self.background_worker else {"worker": "manual", "worker_session_id": None}
        return {"batch_id": batch_id, "state": "queued", **worker}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT j.batch_id,j.state,b.project_id FROM jobs j JOIN batches b USING(batch_id) WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            if row["state"] == "completed":
                return {"job_id": job_id, "state": "completed", "changed": False}
            connection.execute("UPDATE jobs SET state='cancelled',stage='cancelled',updated_at=? WHERE job_id=?", (_now(), job_id))
            remaining = connection.execute("SELECT COUNT(*) FROM jobs WHERE batch_id=? AND state NOT IN ('completed','failed','cancelled')", (row["batch_id"],)).fetchone()[0]
            if not remaining:
                connection.execute("UPDATE batches SET state='cancelled',updated_at=? WHERE batch_id=?", (_now(), row["batch_id"]))
            self._event(connection, "pipeline.cancelled", project_id=row["project_id"], batch_id=row["batch_id"], job_id=job_id, payload={})
        return {"job_id": job_id, "state": "cancelled", "changed": True}

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT j.state,j.batch_id,b.project_id FROM jobs j JOIN batches b USING(batch_id) WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            if row["state"] not in {"failed", "cancelled"}:
                return {"job_id": job_id, "state": row["state"], "changed": False}
            connection.execute("UPDATE jobs SET state='retrying',stage='retrying',error_json=NULL,updated_at=? WHERE job_id=?", (_now(), job_id))
            connection.execute("UPDATE batches SET state='queued',pause_requested=0,updated_at=? WHERE batch_id=?", (_now(), row["batch_id"]))
            connection.execute("UPDATE jobs SET state='queued',updated_at=? WHERE job_id=?", (_now(), job_id))
            self._event(connection, "pipeline.recovering", project_id=row["project_id"], batch_id=row["batch_id"], job_id=job_id,
                        payload={"reason": "retry", "worker_restart_required": True})
        worker = self.start_batch(row["batch_id"]) if self.background_worker else {"worker": "manual", "worker_session_id": None}
        return {"job_id": job_id, "batch_id": row["batch_id"], "state": "queued", "changed": True, **worker}

    def snapshot(self, *, project_id: str | None = None, batch_id: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            context = self._active_context(connection)
            if batch_id:
                owner = connection.execute("SELECT project_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
                if owner is None:
                    raise KeyError(f"batch not found: {batch_id}")
                scope_project_id = owner["project_id"]
            else:
                scope_project_id = project_id or context.get("active_project_id")
            if not scope_project_id:
                latest = connection.execute(
                    "SELECT project_id FROM projects WHERE state='open' ORDER BY updated_at DESC,created_at DESC LIMIT 1"
                ).fetchone()
                scope_project_id = latest["project_id"] if latest else None
            projects = [dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY created_at").fetchall()]
            sources = [dict(row) for row in connection.execute(
                """SELECT s.*,COALESCE(ps.source_language,s.source_language) AS project_source_language
                     FROM sources s JOIN project_sources ps USING(source_id)
                    WHERE ps.project_id=? ORDER BY ps.linked_at""",
                (scope_project_id,),
            ).fetchall()] if scope_project_id else []
            for source in sources:
                try:
                    self._generate_source_previews(
                        str(source["source_id"]), Path(str(source["source_path"])), int(source["page_count"])
                    )
                except (OSError, ValueError, RuntimeError):
                    pass
                preview = self.root / "previews" / source["source_id"] / "preview_manifest.json"
                if preview.is_file():
                    preview_manifest = json.loads(preview.read_text("utf-8"))
                    source["cover_asset_id"] = preview_manifest.get("cover_asset_id")
                    source["thumbnails"] = [
                        {key: item.get(key) for key in (
                            "page", "asset_id", "thumbnail_asset_id", "sha256", "thumbnail_sha256"
                        )}
                        for item in preview_manifest.get("thumbnails", [])
                    ]
            if batch_id:
                batches = [dict(row) for row in connection.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchall()]
            elif scope_project_id:
                batches = [dict(row) for row in connection.execute("SELECT * FROM batches WHERE project_id=? ORDER BY created_at", (scope_project_id,)).fetchall()]
            else:
                batches = []
            if batch_id:
                jobs = [dict(row) for row in connection.execute(
                    "SELECT j.*,b.project_id,b.config_json FROM jobs j JOIN batches b USING(batch_id) WHERE j.batch_id=? ORDER BY j.created_at,j.job_id",
                    (batch_id,),
                ).fetchall()]
            elif scope_project_id:
                jobs = [dict(row) for row in connection.execute(
                    "SELECT j.*,b.project_id,b.config_json FROM jobs j JOIN batches b USING(batch_id) WHERE b.project_id=? ORDER BY j.created_at,j.job_id",
                    (scope_project_id,),
                ).fetchall()]
            else:
                jobs = []
            for job in jobs:
                job["error"] = json.loads(job.pop("error_json")) if job.get("error_json") else None
                job["usage"] = json.loads(job.pop("usage_json") or "{}")
                workspace = self.root / "workspaces" / job["project_id"] / str(job.get("workspace_id") or job["source_id"])
                details: dict[str, Any] = {"workspace": str(workspace), "artifacts": {}}
                for name, relative in {
                    "workspace_manifest": "bookflow_workspace.json",
                    "inspection": "data/inspection_report.json",
                    "page_intake": "data/page_intake_summary.json",
                    "book_structure": "data/book_structure.json",
                    "segmentation_plan": "data/segmentation_plan.json",
                    "publication_reconstruction": "data/publication_reconstruction.json",
                    "translation_plan": "data/translation_plan.json",
                    "validation": "data/validation_report.json",
                    "build": "output/build_manifest.json",
                }.items():
                    artifact = workspace / relative
                    if artifact.is_file():
                        try:
                            details["artifacts"][name] = json.loads(artifact.read_text("utf-8"))
                        except (OSError, json.JSONDecodeError):
                            details["artifacts"][name] = {"status": "unreadable"}
                if job.get("output_path"):
                    manifest = Path(job["output_path"]) / "output_manifest.json"
                    if manifest.is_file():
                        details["output_manifest"] = json.loads(manifest.read_text("utf-8"))
                    artifact_manifest = Path(job["output_path"]) / "artifact_manifest.json"
                    if artifact_manifest.is_file():
                        details["artifact_manifest"] = json.loads(artifact_manifest.read_text("utf-8"))
                job["pipeline_details"] = details
            last_sequence = int(connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0])
        counts: dict[str, int] = {}
        for job in jobs: counts[job["state"]] = counts.get(job["state"], 0) + 1
        active_batch = next((item for item in batches if item["batch_id"] == context.get("active_batch_id")), batches[-1] if batches else None)
        active_project = next((item for item in projects if item["project_id"] == scope_project_id), None)
        current = next((job for job in jobs if job["job_id"] == context.get("active_job_id")), None)
        if current is None:
            current = next((job for job in jobs if job["state"] in {"running", "pausing", "retrying", "recovering", "cancelling"}),
                           next((job for job in jobs if job["state"] in {"queued", "paused"}), None))
        completed = counts.get("completed", 0); total = len(jobs)
        warnings = [{"warning_id": f"job:{job['job_id']}", "type": "job_failed", "job_id": job["job_id"]} for job in jobs if job["state"] == "failed"]
        return {
            "schema_version": "bookflow-snapshot-v1.2", "contract_version": CONTRACT_VERSION,
            "snapshot_version": last_sequence, "generated_at": _now(), "backend_version": CONTRACT_VERSION,
            "connection_status": "connected", "capabilities": self.capabilities(), "provider_status": self._provider_status(jobs),
            "active_context": {**context, "active_project_id": scope_project_id},
            "active_project": active_project, "projects": projects, "sources": sources,
            "active_batch": active_batch, "batches": batches, "jobs": jobs,
            "queue": counts, "pipeline_phase": active_batch["state"] if active_batch else "idle",
            "current_stage": current.get("stage") if current else None, "aggregate_progress": (completed / total if total else 0),
            "current_item": current.get("job_id") if current else None, "total_items": total,
            "can_pause": bool(current and current["state"] == "running"),
            "can_resume": bool(current and current["state"] in {"paused", "queued"} and active_batch and active_batch["state"] == "paused"),
            "can_cancel": bool(current and current["state"] not in TERMINAL_JOB_STATES),
            "can_retry": bool(current and current["state"] in {"failed", "cancelled"}),
            "pause_requested": bool(active_batch and active_batch.get("pause_requested")), "cancel_requested": False,
            "last_checkpoint": max((job.get("updated_at") for job in jobs), default=None),
            "warnings": warnings, "errors": [job["error"] for job in jobs if job.get("error")],
            "outputs": [
                {"job_id": job["job_id"], "artifact_id": artifact["artifact_id"],
                 "format": Path(str(artifact["relative_path"])).suffix.lstrip(".").lower(),
                 "display_name": Path(str(artifact["relative_path"])).name,
                 "role": artifact["role"], "mime_type": artifact["mime_type"],
                 "size": artifact["size"], "sha256": artifact["sha256"],
                 "version": artifact["version"],
                 "build_id": (job.get("pipeline_details") or {}).get("artifact_manifest", {}).get("build_id"),
                 "generated_at": (job.get("pipeline_details") or {}).get("artifact_manifest", {}).get("generated_at"),
                 "status": "ready", "openable": True}
                for job in jobs
                if not context.get("active_job_id") or job["job_id"] == context.get("active_job_id")
                for artifact in ((job.get("pipeline_details") or {}).get("artifact_manifest", {}).get("artifacts", []))
                if artifact.get("role") in {
                    "source_markdown", "target_markdown", "bilingual_markdown",
                    "source_docx", "source_pdf", "target_docx", "target_pdf",
                    "bilingual_docx", "bilingual_pdf",
                }
            ],
            "usage_summary": self._usage_summary(jobs), "last_event_sequence": last_sequence,
            "sequence": last_sequence,
            "web_assist_packages": self.web_assist.list_packages(
                scope_project_id, context.get("active_source_id"),
            ),
            "web_assist_history": self.web_assist.history(scope_project_id),
            "recent_events": self.events(after_sequence=max(0, last_sequence - 200), project_id=scope_project_id),
            "provider_configuration": self._provider_configuration(),
        }

    @staticmethod
    def _usage_summary(jobs: list[dict[str, Any]]) -> dict[str, Any]:
        summary = {"request_count": 0, "retry_count": 0, "latency_seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for job in jobs:
            summary["request_count"] += int(job.get("request_count", 0)); summary["retry_count"] += int(job.get("retry_count", 0)); summary["latency_seconds"] += float(job.get("latency_seconds", 0))
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"): summary[key] += int(job.get("usage", {}).get(key, 0) or 0)
        return summary

    @staticmethod
    def _provider_status(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        values = {(job.get("provider_id"), job.get("model_alias")) for job in jobs if job.get("provider_id")}
        return [{"provider_id": provider, "model_alias": model, "status": "used"} for provider, model in sorted(values)]

    def resolve_asset(self, asset_id: str) -> dict[str, Any]:
        """Return a restart-stable data URL for an asset owned by the active source/job."""
        with self._connect() as connection:
            context = self._active_context(connection)
        source_id = context.get("active_source_id")
        if source_id:
            preview_root = (self.root / "previews" / source_id).resolve()
            manifest_path = preview_root / "preview_manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text("utf-8"))
                for record in manifest.get("thumbnails", []):
                    if asset_id == record.get("asset_id"):
                        path = Path(record["path"]).resolve(); expected = record["sha256"]
                    elif asset_id == record.get("thumbnail_asset_id"):
                        path = Path(record["thumbnail_path"]).resolve(); expected = record["thumbnail_sha256"]
                    else:
                        continue
                    try:
                        path.relative_to(preview_root)
                    except ValueError as exc:
                        raise PermissionError("source asset escapes the active source root") from exc
                    if not path.is_file() or _sha256(path) != expected:
                        raise FileNotFoundError("source asset is missing or changed")
                    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    return {"asset_id": asset_id, "mime_type": mime_type, "sha256": expected,
                            "page_number": record.get("page"), "data_url": f"data:{mime_type};base64,{encoded}"}
        resolved = self._active_artifact_resolver(asset_id).resolve(asset_id)
        encoded = base64.b64encode(resolved.path.read_bytes()).decode("ascii")
        return {"asset_id": asset_id, "mime_type": resolved.mime_type, "sha256": resolved.sha256,
                "build_id": resolved.build_id, "role": resolved.role,
                "data_url": f"data:{resolved.mime_type};base64,{encoded}"}

    def _active_artifact_resolver(self, identifier: str | None = None) -> ArtifactResolver:
        with self._connect() as connection:
            context = self._active_context(connection)
            project_id = context.get("active_project_id")
            if not project_id:
                raise RuntimeError("no active project")
            rows = connection.execute(
                """SELECT j.job_id,j.output_path FROM jobs j JOIN batches b USING(batch_id)
                    WHERE b.project_id=? AND j.output_path IS NOT NULL
                 ORDER BY CASE WHEN j.job_id=? THEN 0 ELSE 1 END,j.updated_at DESC""",
                (project_id, context.get("active_job_id")),
            ).fetchall()
        for row in rows:
            manifest_path = Path(row["output_path"]) / "artifact_manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text("utf-8"))
            if manifest.get("project_id") != project_id or manifest.get("job_id") != row["job_id"]:
                continue
            resolver = ArtifactResolver(manifest)
            if identifier is None:
                return resolver
            try:
                resolver.resolve(identifier)
                return resolver
            except KeyError:
                continue
        raise KeyError("artifact is not present in the active project")

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        resolved = self._active_artifact_resolver(artifact_id).resolve(artifact_id)
        if resolved.mime_type not in {"text/markdown", "text/plain", "text/csv", "application/json"}:
            raise ValueError("artifact is not a readable text format")
        content = resolved.path.read_bytes().decode("utf-8")
        return {"artifact_id": artifact_id, "content": content, "encoding": "utf-8",
                "sha256": resolved.sha256, "build_id": resolved.build_id, "role": resolved.role}

    def render_artifact_page(self, artifact_id: str, page_number: int) -> dict[str, Any]:
        resolved = self._active_artifact_resolver(artifact_id).resolve(artifact_id)
        if resolved.mime_type != "application/pdf":
            raise ValueError("artifact page rendering requires a PDF")
        with fitz.open(resolved.path) as document:
            page_count = len(document)
            if page_number < 1 or page_number > page_count:
                raise ValueError(f"page_number must be between 1 and {page_count}")
            pixmap = document[page_number - 1].get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
            payload = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
            return {
                "artifact_id": artifact_id,
                "page_number": page_number,
                "page_count": page_count,
                "data_url": f"data:image/png;base64,{payload}",
                "width": pixmap.width,
                "height": pixmap.height,
                "sha256": resolved.sha256,
                "build_id": resolved.build_id,
                "role": resolved.role,
            }

    @staticmethod
    def _open_windows_path(path: Path, *, reveal: bool = False) -> dict[str, Any]:
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        if reveal:
            if not path.is_file():
                raise ValueError("reveal requires a file")
            completed = subprocess.run(["explorer.exe", f"/select,{path}"], check=False, timeout=10)
            if completed.returncode != 0:
                raise RuntimeError(f"Explorer returned {completed.returncode}")
            action = "reveal"
        else:
            os.startfile(path)  # type: ignore[attr-defined]
            action = "open"
        return {"action": action, "target": str(path), "os_result": "requested"}

    def open_artifact(self, artifact_id: str) -> dict[str, Any]:
        return self._open_windows_path(self._active_artifact_resolver(artifact_id).resolve(artifact_id).path)

    def reveal_artifact(self, artifact_id: str) -> dict[str, Any]:
        return self._open_windows_path(
            self._active_artifact_resolver(artifact_id).resolve(artifact_id).path, reveal=True
        )

    def artifact_path(self, artifact_id: str) -> dict[str, Any]:
        resolved = self._active_artifact_resolver(artifact_id).resolve(artifact_id)
        result = {"artifact_id": artifact_id, "target": str(resolved.path), "sha256": resolved.sha256,
                  "build_id": resolved.build_id, "role": resolved.role,
                  "mime_type": resolved.mime_type}
        if resolved.mime_type == "application/pdf":
            with fitz.open(resolved.path) as document:
                result["page_count"] = len(document)
        return result

    def open_output_root(self) -> dict[str, Any]:
        resolver = self._active_artifact_resolver()
        return self._open_windows_path(resolver.root)

    def reveal_log_file(self) -> dict[str, Any]:
        resolver = self._active_artifact_resolver()
        log = next((item for item in resolver.manifest.get("logs", [])), None)
        if log is None:
            fallback = resolver.root / "processing_report.md"
            return self._open_windows_path(fallback, reveal=True)
        return self.reveal_artifact(str(log["artifact_id"]))

    def events(
        self,
        *,
        after_sequence: int = 0,
        batch_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM events WHERE sequence>?"
        params: list[Any] = [after_sequence]
        if batch_id:
            query += " AND batch_id=?"; params.append(batch_id)
        if project_id:
            query += " AND project_id=?"; params.append(project_id)
        query += " ORDER BY sequence"
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(query, params).fetchall()]
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
            row["schema_version"] = "bookflow-event-v1.2"; row["contract_version"] = CONTRACT_VERSION
            row["timestamp"] = row.pop("created_at")
        return rows

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema_version": "bookflow-capabilities-v1.2", "contract_version": CONTRACT_VERSION,
            "supported_languages": list(SUPPORTED_LANGUAGES),
            "supported_source_formats": sorted(SUPPORTED_SOURCE_FORMATS),
            "direct_commands": ["getCapabilities", "getSnapshot", "createProject", "openProject", "closeProject", "importSources", "selectSourceDocument", "configureProvider", "startPipeline", "pausePipeline", "resumePipeline", "cancelPipeline", "retryFailedStage", "recoverFromCheckpoint", "refreshActivePublication", "rebuildActiveOutputs", "exportOutputs", "resolveAsset", "readArtifact", "renderArtifactPage", "openArtifact", "revealArtifact", "getArtifactPath", "openOutputFolder", "revealLogFile", "acknowledgeWarning", "createWebAssistPackage", "listWebAssistPackages", "getWebAssistPackage", "validateWebAssistImport", "previewWebAssistDiff", "applyWebAssistImport", "discardWebAssistPackage", "undoWebAssistApply", "testProviderConnection"],
            "adapter_commands": [],
            "transport_deferred_commands": ["openWebAssistPackageFolder"],
            "unsupported_capabilities": ["immediate_stage_preemption", "parallel_workers", "paid_provider_by_default"],
            "mock_provider_default": False,
            "production_pipeline": "phase13.5+validated-phase13.6",
            "backend_process_model": "persistent_sidecar" if self.background_worker else "in_process_test_or_cli",
            "closed_project_policy": {"read": True, "import": False, "new_tasks": False, "recovery": True},
            "checkpoint_recovery": "durable job state and workspace caches; interrupted running jobs return to queued",
            "supportsWebAssist": True,
            "supportsGlossaryReviewExport": True,
            "supportsGlossaryReviewImport": True,
            "supportsDifficultPageExport": True,
            "supportsDifficultPageImport": True,
            "supportsWebAssistDiffPreview": True,
            "supportsIncrementalRebuild": True,
            "supportsWebAssistUndo": True,
            "supportsProviderConnectionTest": self.provider_config_path is not None,
            "supportsProviderConfigurationEdit": self.provider_config_path is not None,
        }

    def execute(self, command: str, payload: dict[str, Any], *, command_id: str | None = None, schema_version: str = "1.2") -> dict[str, Any]:
        command_id = command_id or str(payload.get("command_id") or _id("command"))
        cache_key = f"execute:{command_id}"
        if command not in {"getCapabilities", "getSnapshot"}:
            cached = self._command(cache_key)
            if cached is not None: return cached
        if schema_version != "1.2": return self._response(command_id, command, False, error=error_envelope(ValueError("schema mismatch"), code="schema_mismatch", recoverable=False))
        direct = set(self.capabilities()["direct_commands"])
        if command not in direct:
            category = "transport_deferred" if command in self.capabilities()["transport_deferred_commands"] else "adapter_needed" if command in self.capabilities()["adapter_commands"] else "unknown_command"
            return self._response(command_id, command, False, error=error_envelope(ValueError(category), code=category, recoverable=False))
        try:
            required = {"createProject": ["name"], "openProject": ["project_id"], "closeProject": ["project_id"], "importSources": ["paths"], "selectSourceDocument": ["project_id", "source_id"], "startPipeline": ["batch_id"], "pausePipeline": ["batch_id"], "resumePipeline": ["batch_id"], "cancelPipeline": ["job_id"], "retryFailedStage": ["job_id"], "recoverFromCheckpoint": ["batch_id"], "exportOutputs": ["job_id"], "resolveAsset": ["asset_id"], "readArtifact": ["artifact_id"], "renderArtifactPage": ["artifact_id", "page_number"], "openArtifact": ["artifact_id"], "revealArtifact": ["artifact_id"], "getArtifactPath": ["artifact_id"], "acknowledgeWarning": ["warning_id"], "createWebAssistPackage": ["package_type", "project_id", "source_document_id"], "getWebAssistPackage": ["package_id", "source_document_id"], "validateWebAssistImport": ["package_id", "import_path", "source_document_id"], "previewWebAssistDiff": ["package_id", "source_document_id"], "applyWebAssistImport": ["package_id", "source_document_id"], "discardWebAssistPackage": ["package_id", "source_document_id"], "undoWebAssistApply": ["project_id", "source_document_id"]}
            missing = [key for key in required.get(command, []) if key not in payload]
            if missing: raise ValueError(f"missing required fields: {missing}")
            if command == "getCapabilities": result = self.capabilities()
            elif command == "getSnapshot": result = self.snapshot(project_id=payload.get("project_id"), batch_id=payload.get("batch_id"))
            elif command == "createProject": result = self.create_project(payload["name"], project_id=payload.get("project_id"))
            elif command == "openProject": result = self.open_project(payload["project_id"])
            elif command == "closeProject": result = self.close_project(payload["project_id"])
            elif command == "importSources": result = self.import_sources(payload.get("project_id"), [Path(item) for item in payload["paths"]], command_id=f"import:{command_id}", recursive=bool(payload.get("recursive", True)), source_languages=payload.get("source_languages"), pipeline_config=payload.get("pipeline_config"))
            elif command == "selectSourceDocument": result = self.select_source(payload["project_id"], payload["source_id"])
            elif command == "configureProvider": result = self.configure_provider(payload)
            elif command == "startPipeline": result = self.start_batch(payload["batch_id"]) if self.background_worker else self.run_batch(payload["batch_id"])
            elif command == "pausePipeline": result = self.pause_batch(payload["batch_id"])
            elif command == "resumePipeline":
                result = self.resume_batch(payload["batch_id"])
            elif command == "cancelPipeline": result = self.cancel_job(payload["job_id"])
            elif command == "retryFailedStage": result = self.retry_job(payload["job_id"])
            elif command == "recoverFromCheckpoint":
                result = self.recover_batch(payload["batch_id"])
                if not self.background_worker:
                    result = self.run_batch(payload["batch_id"])
            elif command == "refreshActivePublication": result = self._queue_active_publication_refresh()
            elif command == "rebuildActiveOutputs": result = self._rebuild_active_outputs()
            elif command == "exportOutputs": result = self.export_outputs(payload["job_id"])
            elif command == "resolveAsset": result = self.resolve_asset(payload["asset_id"])
            elif command == "readArtifact": result = self.read_artifact(payload["artifact_id"])
            elif command == "renderArtifactPage": result = self.render_artifact_page(payload["artifact_id"], int(payload["page_number"]))
            elif command == "openArtifact": result = self.open_artifact(payload["artifact_id"])
            elif command == "revealArtifact": result = self.reveal_artifact(payload["artifact_id"])
            elif command == "getArtifactPath": result = self.artifact_path(payload["artifact_id"])
            elif command == "openOutputFolder": result = self.open_output_root()
            elif command == "revealLogFile": result = self.reveal_log_file()
            elif command == "acknowledgeWarning": result = self.acknowledge_warning(payload["warning_id"], payload.get("project_id"))
            elif command == "createWebAssistPackage": result = self._create_web_assist_package(payload)
            elif command == "listWebAssistPackages": result = {"packages": self.web_assist.list_packages(payload.get("project_id"), payload.get("source_document_id"))}
            elif command == "getWebAssistPackage":
                result = self._require_web_assist_source(payload)
            elif command == "validateWebAssistImport": result = self._validate_web_assist_import(payload)
            elif command == "previewWebAssistDiff":
                self._require_web_assist_source(payload)
                result = self._preview_web_assist_diff(payload["package_id"])
            elif command == "applyWebAssistImport":
                self._require_web_assist_source(payload)
                result = self._apply_web_assist_import(payload["package_id"])
            elif command == "discardWebAssistPackage":
                self._require_web_assist_source(payload)
                result = self.web_assist.discard_package(payload["package_id"])
            elif command == "undoWebAssistApply":
                result = self._undo_web_assist_apply(
                    payload["project_id"], payload["source_document_id"],
                )
            else:
                reference = str(payload.get("role") or payload.get("provider_id") or "")
                if not reference:
                    raise ValueError("missing required model role")
                result = self.test_provider_connection(reference)
            response = self._response(command_id, command, True, result=result)
        except Exception as exc:
            response = self._response(command_id, command, False, error=error_envelope(exc, code="command_rejected", recoverable=isinstance(exc, (RuntimeError, PermissionError))))
        self._store_command(cache_key, command, response)
        return response

    @staticmethod
    def _response(command_id: str, command: str, accepted: bool, *, result: Any = None, error: Any = None) -> dict[str, Any]:
        return {"schema_version": "bookflow-command-response-v1.2", "contract_version": CONTRACT_VERSION, "command_id": command_id, "command": command, "status": "accepted" if accepted else "rejected", "accepted": accepted, "result": result, "error": error, "timestamp": _now()}

    def _web_assist_context(self, project_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone() is None:
                raise KeyError(f"project not found: {project_id}")
            sources = [dict(row) for row in connection.execute("SELECT s.* FROM sources s JOIN project_sources ps USING(source_id) WHERE ps.project_id=? ORDER BY s.created_at", (project_id,)).fetchall()]
            jobs = [dict(row) for row in connection.execute("SELECT j.* FROM jobs j JOIN batches b USING(batch_id) WHERE b.project_id=? ORDER BY j.created_at", (project_id,)).fetchall()]
        return sources, jobs

    def _web_assist_event(self, event_type: str, project_id: str | None, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            self._event(connection, event_type, project_id=project_id, payload=payload)

    def _require_web_assist_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        package = self.web_assist.get_package(payload["package_id"])
        if package["source_document_id"] != payload.get("source_document_id"):
            raise ValueError("web-assist package does not belong to the active source document")
        return package

    def _create_web_assist_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = payload["project_id"]
        self._web_assist_event("web_assist.export_started", project_id, {"package_type": payload["package_type"]})
        try:
            sources, jobs = self._web_assist_context(project_id)
            result = self.web_assist.create_package(WebAssistExportRequest(payload["package_type"], project_id, payload.get("source_document_id")), sources, jobs)
            if result.package is None:
                value = {
                    "package": None,
                    "files": [],
                    "skipped": True,
                    "reason": result.reason,
                }
                self._web_assist_event(
                    "web_assist.export_completed", project_id,
                    {"package_id": None, "package_type": payload["package_type"],
                     "item_count": 0, "skipped": True, "reason": result.reason},
                )
                return value
            value = {"package": asdict(result.package), "files": result.files}
            self._web_assist_event("web_assist.export_progress", project_id, {"package_id": result.package.package_id, "completed": result.package.item_count, "total": result.package.item_count})
            self._web_assist_event("web_assist.export_completed", project_id, {"package_id": result.package.package_id, "package_type": result.package.package_type, "item_count": result.package.item_count})
            return value
        except Exception as exc:
            self._web_assist_event("web_assist.export_failed", project_id, {"package_type": payload["package_type"], "error": error_envelope(exc)})
            raise

    def _validate_web_assist_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        package = self.web_assist.get_package(payload["package_id"])
        project_id = package["project_id"]
        self._web_assist_event("web_assist.import_started", project_id, {"package_id": payload["package_id"]})
        try:
            self._require_web_assist_source(payload)
            result = self.web_assist.validate_import(WebAssistImportRequest(
                payload["package_id"], payload["import_path"], payload["source_document_id"],
            ))
            value = asdict(result)
            event_type = "web_assist.import_conflict" if result.conflicts else "web_assist.import_validated"
            self._web_assist_event(event_type, project_id, {"package_id": result.package_id, "valid": result.valid, "conflict_count": len(result.conflicts), "change_count": len(result.changes)})
            return value
        except Exception as exc:
            self._web_assist_event("web_assist.import_failed", project_id, {"package_id": payload["package_id"], "error": error_envelope(exc)})
            raise

    def _preview_web_assist_diff(self, package_id: str) -> dict[str, Any]:
        result = self.web_assist.preview_diff(package_id)
        package = self.web_assist.get_package(package_id)
        self._web_assist_event("web_assist.diff_ready", package["project_id"], {"package_id": package_id, "summary": result["summary"]})
        return result

    def _apply_web_assist_import(self, package_id: str) -> dict[str, Any]:
        package = self.web_assist.get_package(package_id)
        result = self.web_assist.apply_import(package_id)
        value = asdict(result)
        value["rebuild"] = self._rebuild_source_after_web_assist(
            package["source_document_id"], project_id=package["project_id"], reason="web_assist_apply"
        )
        self._web_assist_event("web_assist.import_completed", package["project_id"], {"package_id": package_id, "application_id": result.application_id})
        self._web_assist_event("web_assist.corrections_applied", package["project_id"], value)
        return value

    def _undo_web_assist_apply(self, project_id: str, source_document_id: str) -> dict[str, Any]:
        result = self.web_assist.undo_last_apply(project_id, source_document_id)
        result["rebuild"] = self._rebuild_source_after_web_assist(
            result["source_document_id"], project_id=project_id, reason="web_assist_undo"
        )
        self._web_assist_event("web_assist.corrections_undone", project_id, result)
        return result

    def _rebuild_job_outputs(
        self, job: dict[str, Any], *, project_id: str, source_id: str,
        stage: str, reason: str,
    ) -> dict[str, Any]:
        job_id = str(job["job_id"])
        workspace_id = str(job["workspace_id"])
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET state='running',stage=?,progress=0.82,updated_at=? WHERE job_id=?",
                (stage, _now(), job_id),
            )
            self._event(
                connection, "pipeline.stage_started", project_id=project_id,
                batch_id=str(job.get("batch_id") or "") or None, job_id=job_id,
                payload={"stage": stage, "reason": reason, "provider_calls": 0},
            )
        workspace = self.root / "workspaces" / project_id / workspace_id
        if not (workspace / "bookflow_workspace.json").is_file():
            raise RuntimeError("scoped workspace is missing")
        plan = plan_workspace(workspace)
        if plan["pending"]:
            raise RuntimeError("output rebuild requires complete translation caches")
        config = json.loads(job.get("config_json") or "{}")
        formats = tuple(
            str(item).lower() for item in (config.get("output_formats") or ["md", "docx", "pdf"])
            if str(item).lower() in {"md", "docx", "pdf"}
        ) or ("md",)
        build = build_workspace(workspace, formats, layout_mode="publication")
        output = Path(str(job.get("output_path") or (self.root / "outputs" / project_id / workspace_id)))
        output.mkdir(parents=True, exist_ok=True)
        for role, role_manifest in build["roles"].items():
            for fmt, artifact in role_manifest.get("outputs", {}).items():
                path = Path(str(artifact.get("path"))) if isinstance(artifact, dict) and artifact.get("path") else None
                if path and path.is_file():
                    shutil.copy2(path, output / f"{role}.{fmt}")
        atomic_write_json(output / "build_manifest.json", build)
        source_asset_paths: list[tuple[int, Path]] = []
        for path in sorted((output / "assets/source").glob("page-*")) if (output / "assets/source").is_dir() else []:
            match = re.match(r"page-(\d+)", path.name)
            if match and path.is_file():
                source_asset_paths.append((int(match.group(1)), path))
        artifact_manifest = build_artifact_manifest(
            output_root=output, workspace_id=workspace_id, project_id=project_id,
            source_id=source_id, job_id=job_id, source_asset_paths=source_asset_paths,
        )
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET state='completed',stage='completed',progress=1,output_path=?,updated_at=? WHERE job_id=?",
                (str(output), _now(), job_id),
            )
            self._event(
                connection, "pipeline.stage_completed", project_id=project_id,
                batch_id=str(job.get("batch_id") or "") or None, job_id=job_id,
                payload={"stage": stage, "reason": reason, "provider_calls": 0,
                         "build_id": artifact_manifest["build_id"]},
            )
        return {
            "job_id": job_id, "workspace_id": workspace_id, "status": "completed",
            "reason": reason, "provider_calls": 0, "output_path": str(output),
            "build_id": artifact_manifest["build_id"], "roles": sorted(build["roles"]),
        }

    def _rebuild_source_after_web_assist(self, source_id: str, *, project_id: str, reason: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT j.*,s.*,b.config_json,b.project_id FROM jobs j JOIN sources s USING(source_id) "
                "JOIN batches b USING(batch_id) WHERE j.source_id=? AND b.project_id=? "
                "ORDER BY j.updated_at DESC LIMIT 1",
                (source_id, project_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("no production job is available for web-assist rebuild")
            job = dict(row)
        try:
            return self._rebuild_job_outputs(
                job, project_id=project_id, source_id=source_id,
                stage="incremental_rebuild", reason=reason,
            )
        except Exception:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE jobs SET state='failed',stage='incremental_rebuild_failed',updated_at=? WHERE job_id=?",
                    (_now(), job["job_id"]),
                )
            raise

    def _rebuild_active_outputs(self) -> dict[str, Any]:
        with self._connect() as connection:
            context = self._active_context(connection)
            project_id = context.get("active_project_id")
            source_id = context.get("active_source_id")
            job_id = context.get("active_job_id")
            if not project_id or not source_id or not job_id:
                raise RuntimeError("active project, source, and job are required for output rebuild")
            row = connection.execute(
                "SELECT j.job_id,j.batch_id,j.workspace_id,j.output_path,b.config_json,b.project_id,j.source_id "
                "FROM jobs j JOIN batches b USING(batch_id) WHERE j.job_id=? AND b.project_id=? AND j.source_id=?",
                (job_id, project_id, source_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("active output rebuild context does not resolve to a job")
            job = dict(row)
        return self._rebuild_job_outputs(
            job, project_id=str(project_id), source_id=str(source_id),
            stage="output_rebuild", reason="active_output_rebuild",
        )

    def _queue_active_publication_refresh(self) -> dict[str, Any]:
        with self._connect() as connection:
            context = self._active_context(connection)
            project_id = context.get("active_project_id")
            source_id = context.get("active_source_id")
            batch_id = context.get("active_batch_id")
            job_id = context.get("active_job_id")
            if not all((project_id, source_id, batch_id, job_id)):
                raise RuntimeError("active project, source, batch, and job are required for publication refresh")
            row = connection.execute(
                "SELECT j.job_id FROM jobs j JOIN batches b USING(batch_id) "
                "WHERE j.job_id=? AND j.batch_id=? AND j.source_id=? AND b.project_id=?",
                (job_id, batch_id, source_id, project_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("active publication refresh context does not resolve to a job")
            now = _now()
            connection.execute(
                "UPDATE jobs SET state='queued',stage='publication_refresh_queued',progress=0,error_json=NULL,updated_at=? "
                "WHERE job_id=?", (now, job_id),
            )
            connection.execute(
                "UPDATE batches SET state='queued',pause_requested=0,updated_at=? WHERE batch_id=?",
                (now, batch_id),
            )
            self._event(connection, "pipeline.queued", project_id=str(project_id), batch_id=str(batch_id),
                        job_id=str(job_id), payload={"reason": "publication_refresh", "provider_calls": 0})
        return {"project_id": str(project_id), "source_id": str(source_id), "batch_id": str(batch_id),
                "job_id": str(job_id), "state": "queued", "provider_calls": 0}

    def _provider_configuration(self) -> list[dict[str, Any]]:
        if self.provider_config_path is None:
            return []
        try:
            registry = ProviderRegistry.load(self.provider_config_path)
            test_path = self.root / "provider_connection_tests.json"
            tests = json.loads(test_path.read_text("utf-8")) if test_path.is_file() else {}
            validated = {item["provider_id"]: item for item in registry.validate()["providers"]}
            roles = (("language", "语言模型", registry.active_text),
                     ("vision", "视觉模型", registry.active_vision))
            result = []
            for role, display_name, provider_id in roles:
                item = validated.get(provider_id or "")
                if item is None:
                    result.append({"role": role, "display_name": display_name, "base_url": "", "model": "",
                                   "credential_present": False, "credential_source": "not_configured",
                                   "configured": False, "valid": False, "errors": ["active model role is not configured"],
                                   "connection_status": "未配置", "last_test_at": None})
                    continue
                role_test = tests.get(provider_id, {})
                result.append({
                    "role": role, "display_name": display_name,
                    "base_url": item.get("base_url") or "", "model": item["model"],
                    "credential_present": item["credential_present"],
                    "credential_source": item.get("credential_source", "not_configured"),
                    "configured": item["credential_present"] and item["valid"],
                    "valid": item["valid"], "errors": item["errors"],
                    "connection_status": role_test.get("status", "未测试"),
                    "last_test_at": role_test.get("tested_at"),
                    "last_test_latency_seconds": role_test.get("latency_seconds"),
                })
            return result
        except Exception as exc:
            return [{"role": role, "display_name": label, "base_url": "", "model": "", "valid": False,
                     "credential_present": False, "errors": [str(exc)[:300]]}
                    for role, label in (("language", "语言模型"), ("vision", "视觉模型"))]

    @staticmethod
    def _provider_id_for_role(registry: ProviderRegistry, reference: str) -> str:
        if reference == "language":
            provider_id = registry.active_text
        elif reference == "vision":
            provider_id = registry.active_vision
        else:
            provider_id = reference
        if not provider_id or provider_id not in registry.profiles:
            raise KeyError(f"model role is not configured: {reference}")
        return provider_id

    def test_provider_connection(self, reference: str) -> dict[str, Any]:
        if self.provider_config_path is None:
            raise RuntimeError("provider configuration is unavailable")
        registry = ProviderRegistry.load(self.provider_config_path)
        provider_id = self._provider_id_for_role(registry, reference)
        profile = registry.profiles[provider_id]
        if profile.provider_type == "mock":
            result = {"provider_id": provider_id, "model": profile.model, "capability": "mock", "ok": True,
                      "latency_seconds": 0.0, "tested_at": _now(), "status": "连接成功"}
            self._record_provider_test(provider_id, result)
            return result
        capability = "vision" if "vision" in profile.capabilities else "text"
        client = registry.client(profile)
        attempt_id = _id("provider_attempt")
        started_at = _now()
        started = time.monotonic()
        if capability == "vision":
            from PIL import Image
            probe = self.root / "provider-connection-probe.png"
            Image.new("RGB", (32, 32), "white").save(probe)
            request_descriptor = {
                "capability": "vision", "prompt_contract": "json_ok_true",
                "image_sha256": _sha256(probe), "image_size": [32, 32],
            }
            purpose = "model_service_vision_image_input_test"
            page_or_segment_id = "connection-probe:image"
        else:
            request_descriptor = {"capability": "text", "payload": {"connection_test": True},
                                  "response_contract": {"ok": True}}
            purpose = "model_service_language_structured_output_test"
            page_or_segment_id = "connection-probe:text"
        with self._connect() as connection:
            context = self._active_context(connection)
        dispatch = {
            "attempt_id": attempt_id, "project_id": context.get("active_project_id"),
            "job_id": context.get("active_job_id"), "stage": "provider_connection_test",
            "provider_role": reference if reference in {"language", "vision"} else capability,
            "provider": provider_id, "model": profile.model,
            "page_or_segment_id": page_or_segment_id, "purpose": purpose,
            "start": started_at, "end": None, "latency": None, "status": "dispatching",
            "retry_number": 0, "usage": {}, "request_hash": _json_sha256(request_descriptor),
            "response_hash": None, "error_code": None,
        }
        self._record_provider_attempt(dispatch)
        raw: dict[str, Any] | None = None
        try:
            if capability == "vision":
                raw = client.vision_json(prompt='Return JSON exactly as {"ok":true}.', image_path=probe)
            else:
                raw = client.text_json(
                    system_prompt='Return JSON exactly as {"ok":true}.',
                    payload={"connection_test": True},
                )
            if parse_model_json(raw).get("ok") is not True:
                raise RuntimeError("provider returned an invalid connection-test response")
        except Exception as exc:
            metrics = dict(client.last_request_metrics)
            self._record_provider_attempt({
                **dispatch, **metrics, "status": "failed", "end": metrics.get("end", _now()),
                "latency": metrics.get("latency", round(time.monotonic() - started, 3)),
                "response_hash": _json_sha256(raw) if raw is not None else None,
                "error_code": metrics.get("error_code", type(exc).__name__),
            })
            raise
        metrics = dict(client.last_request_metrics)
        self._record_provider_attempt({
            **dispatch, **metrics, "status": "validated", "end": metrics.get("end", _now()),
            "latency": metrics.get("latency", round(time.monotonic() - started, 3)),
            "response_hash": _json_sha256(raw), "error_code": None,
        })
        result = {"provider_id": provider_id, "model": profile.model, "capability": capability, "ok": True,
                  "latency_seconds": round(time.monotonic() - started, 3), "tested_at": _now(), "status": "连接成功"}
        self._record_provider_test(provider_id, result)
        return result

    def _record_provider_attempt(self, item: dict[str, Any]) -> None:
        path = self.root / "provider_attempts.jsonl"
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    def _record_provider_test(self, provider_id: str, result: dict[str, Any]) -> None:
        path = self.root / "provider_connection_tests.json"
        values = json.loads(path.read_text("utf-8")) if path.is_file() else {}
        values[provider_id] = {key: result.get(key) for key in
                               ("model", "capability", "latency_seconds", "tested_at", "status")}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", "utf-8")
        temporary.replace(path)

    def configure_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.provider_config_path is None:
            raise RuntimeError("provider configuration is unavailable")
        unknown = set(payload) - {"role", "provider_id", "base_url", "model"}
        if unknown:
            raise ValueError(f"unsupported provider configuration fields: {sorted(unknown)}")
        data = yaml.safe_load(self.provider_config_path.read_text("utf-8")) or {}
        providers = data.get("providers") or {}
        reference = str(payload.get("role") or payload.get("provider_id") or "")
        registry = ProviderRegistry.load(self.provider_config_path)
        provider_id = self._provider_id_for_role(registry, reference)
        if provider_id not in providers:
            raise KeyError(f"provider not found: {provider_id}")
        for field in ("base_url", "model"):
            if field in payload:
                value = str(payload[field]).strip()
                if not value:
                    raise ValueError(f"{field} cannot be empty")
                providers[provider_id][field] = value
        temporary = self.provider_config_path.with_suffix(self.provider_config_path.suffix + ".tmp")
        temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
        ProviderRegistry.load(temporary).validate()
        temporary.replace(self.provider_config_path)
        self._web_assist_event("capabilities.changed", None, {"provider_id": provider_id})
        return {"role": reference if reference in {"language", "vision"} else None,
                "saved": True, "credential_changed": False}

    def select_source(self, project_id: str, source_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM project_sources WHERE project_id=? AND source_id=?", (project_id, source_id)).fetchone() is None: raise KeyError("source is not linked to project")
            connection.execute("UPDATE projects SET active_source_id=?,updated_at=? WHERE project_id=?", (source_id, _now(), project_id))
            self._activate_project_source(connection, project_id=project_id, source_id=source_id)
        return {"project_id": project_id, "source_id": source_id, "selected": True}

    def export_outputs(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection: row = connection.execute("SELECT output_path,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None: raise KeyError("job not found")
        if row["state"] != "completed" or not row["output_path"]: raise RuntimeError("outputs are not ready")
        path = Path(row["output_path"]); return {"job_id": job_id, "output_path": str(path), "resources": [str(item) for item in sorted(path.rglob("*")) if item.is_file()]}

    def acknowledge_warning(self, warning_id: str, project_id: str | None) -> dict[str, Any]:
        with self._connect() as connection: connection.execute("INSERT OR REPLACE INTO warning_acknowledgements(warning_id,project_id,acknowledged_at) VALUES(?,?,?)", (warning_id, project_id, _now()))
        return {"warning_id": warning_id, "acknowledged": True}

    def _command(self, command_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT response_json FROM commands WHERE command_id=?", (command_id,)).fetchone()
        return json.loads(row["response_json"]) if row else None

    def _store_command(self, command_id: str, command: str, response: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO commands(command_id,command_name,response_json,created_at) VALUES(?,?,?,?)", (command_id, command, json.dumps(response, ensure_ascii=False), _now()))

    @staticmethod
    def _event(connection: sqlite3.Connection, event_type: str, *, project_id: str | None = None, batch_id: str | None = None, job_id: str | None = None, payload: dict[str, Any]) -> None:
        if project_id is None and batch_id is not None:
            owner = connection.execute("SELECT project_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            project_id = owner["project_id"] if owner else None
        if project_id is None and job_id is not None:
            owner = connection.execute(
                "SELECT b.project_id FROM jobs j JOIN batches b USING(batch_id) WHERE j.job_id=?", (job_id,)
            ).fetchone()
            project_id = owner["project_id"] if owner else None
        connection.execute("INSERT INTO events(event_id,event_type,project_id,batch_id,job_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?)", (_id("event"), event_type, project_id, batch_id, job_id, json.dumps(payload, ensure_ascii=False), _now()))
        if event_type != "snapshot.updated":
            connection.execute("INSERT INTO events(event_id,event_type,project_id,batch_id,job_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?)", (_id("event"), "snapshot.updated", project_id, batch_id, job_id, json.dumps({"caused_by": event_type}, ensure_ascii=False), _now()))
