"""Local, token-protected HTTP interface over Bookflow workspace services."""

from __future__ import annotations

import json
import os
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from .credential_store import CredentialStore
from .manual_review import export_review_package, import_patch, load_records, validate_patch
from .multilingual_workspace import (
    build_workspace, control_workspace, create_workspace, inspect_workspace, plan_workspace,
    status_workspace, validate_workspace,
)
from .provider_registry import ProviderRegistry, RegistryTranslationProvider
from .publication_structure import run_structure_workspace


ROOT = Path(__file__).resolve().parent / "ui_prototypes"
SESSION_TOKEN = secrets.token_urlsafe(32)


def _body(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    value = json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
    if not isinstance(value, dict):
        raise ValueError("request body must be an object")
    return value


def _workspace(data: dict[str, Any]) -> Path:
    if not data.get("workspace"):
        raise ValueError("workspace is required")
    return Path(str(data["workspace"])).resolve()


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() == "api_key" or _contains_secret_field(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret_field(item) for item in value)
    return False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _json(self, value: Any, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        origin_ok = not origin or origin in {f"http://{host}", f"https://{host}"}
        return origin_ok and secrets.compare_digest(self.headers.get("X-Bookflow-Token", ""), SESSION_TOKEN)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/session":
                self._json({"token": SESSION_TOKEN, "local_only": True}); return
            if parsed.path == "/api/health":
                self._json({"ok": True, "backend": "bookflow"}); return
            if parsed.path == "/api/status":
                self._json(status_workspace(Path(query["workspace"][0]))); return
            if parsed.path == "/api/review/status":
                workspace = Path(query["workspace"][0])
                path = workspace / "data/structure_review_objects.json"
                records = load_records(path) if path.is_file() else []
                self._json({"pending": sum(x.get("review_status") == "pending" for x in records),
                            "objects": records}); return
            if parsed.path == "/api/renderers":
                from .renderer_backend import detect_renderer, load_renderer_config
                config = Path(query["config"][0]) if query.get("config") else None
                self._json(detect_renderer(load_renderer_config(config))); return
            if parsed.path == "/api/providers":
                config = Path(query["config"][0])
                self._json(ProviderRegistry.load(config).validate()); return
            if parsed.path == "/":
                self.path = "/index.html"
            super().do_GET()
        except Exception as exc:
            self._json({"error": type(exc).__name__, "message": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json({"error": "unauthorized"}, HTTPStatus.FORBIDDEN); return
        try:
            data = _body(self)
            action = urlparse(self.path).path
            workspace = _workspace(data) if data.get("workspace") else None
            if action == "/api/workspace/create":
                result = create_workspace(Path(data["workspace"]), Path(data["source_pdf"]),
                    data.get("source_language", "auto"), data["target_language"],
                    output_directory=Path(data["output_directory"]) if data.get("output_directory") else None,
                    layout_mode=data.get("layout_mode", "text"),
                    bilingual_layout=data.get("bilingual_layout", "stacked"),
                    output_role=data.get("output_role", "all"),
                    metadata={key: data[key] for key in ("book_title", "author", "volume", "year",
                              "source_institution", "rights_notice") if data.get(key)})
            elif action == "/api/inspect": result = inspect_workspace(workspace)
            elif action == "/api/plan": result = plan_workspace(workspace)
            elif action == "/api/status": result = status_workspace(workspace)
            elif action == "/api/validate": result = validate_workspace(workspace)
            elif action in {"/api/pause", "/api/cancel"}:
                result = control_workspace(workspace, action.rsplit("/", 1)[-1])
            elif action in {"/api/build", "/api/rebuild", "/api/render"}:
                result = build_workspace(workspace, tuple(data.get("formats", ["md", "docx", "pdf"])),
                    layout_mode=data.get("layout_mode"), bilingual_layout=data.get("bilingual_layout"),
                    pdf_renderer=data.get("pdf_renderer", "native_pdf"),
                    renderer_config=Path(data["renderer_config"]) if data.get("renderer_config") else None)
            elif action in {"/api/translate", "/api/resume"}:
                registry = ProviderRegistry.load(Path(data["provider_config"]))
                profile = registry.get(data.get("provider"), "text")
                from .multilingual_workspace import translate_workspace
                result = translate_workspace(workspace, RegistryTranslationProvider(workspace, registry.client(profile)),
                    provider_name=profile.provider_id, model=profile.model,
                    batch_size=int(data.get("batch_size", 8)), max_units=data.get("max_units"))
            elif action == "/api/structure":
                registry = ProviderRegistry.load(Path(data["provider_config"]))
                result = run_structure_workspace(workspace, registry, provider_id=data.get("provider"),
                                                 model=data.get("model"), max_batches=data.get("max_batches"))
            elif action == "/api/providers/save":
                config = Path(data["config"]).resolve()
                payload = data["payload"]
                if _contains_secret_field(payload):
                    raise ValueError("provider config must contain credential aliases, not API keys")
                config.parent.mkdir(parents=True, exist_ok=True)
                temporary = config.with_suffix(config.suffix + ".tmp")
                temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
                temporary.replace(config)
                result = {"saved": True, "config": str(config)}
            elif action in {"/api/providers/validate", "/api/providers/test"}:
                registry = ProviderRegistry.load(Path(data["config"]))
                result = registry.validate()
                if action.endswith("/test") and result["valid"]:
                    profile = registry.get(data.get("provider"), data.get("capability", "text"))
                    result = {"provider_id": profile.provider_id, "model": profile.model,
                              "credential_present": profile.public_dict()["credential_present"], "valid": True}
            elif action in {"/api/credentials/set", "/api/credentials/delete", "/api/credentials/test"}:
                store = CredentialStore(); alias = str(data["alias"])
                if action.endswith("/set"):
                    result = store.set(alias, str(data["secret"]), process_only=bool(data.get("process_only", False)))
                elif action.endswith("/delete"): result = store.delete(alias)
                else: result = store.test(alias)
            elif action in {"/api/renderers/detect", "/api/renderers/test"}:
                from .renderer_backend import detect_renderer, load_renderer_config, test_renderer
                config = load_renderer_config(Path(data["config"]) if data.get("config") else None)
                result = test_renderer(config) if action.endswith("/test") else detect_renderer(config)
            elif action == "/api/review/export":
                manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
                result = export_review_package(workspace / "data/structure_review_objects.json",
                    Path(data["output"]), source_pages_pdf=Path(manifest["source_pdf"]), complete=True)
            elif action == "/api/review/validate":
                result = validate_patch(Path(data["patch"]), workspace / "data/structure_review_objects.json")
            elif action == "/api/review/import":
                result = import_patch(Path(data["patch"]), workspace / "data/structure_review_objects.json",
                    output_path=workspace / "manual_review/imported_objects.json",
                    provenance_path=workspace / "manual_review/provenance.json",
                    dry_run=bool(data.get("dry_run", True)))
            elif action == "/api/open":
                path = Path(data["path"]).resolve()
                if not path.exists(): raise FileNotFoundError(path)
                os.startfile(path)  # type: ignore[attr-defined]
                result = {"opened": True, "path": str(path)}
            elif action in {"/api/dialog/file", "/api/dialog/directory"}:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
                try:
                    selected = (filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
                                if action.endswith("/file") else filedialog.askdirectory())
                finally:
                    root.destroy()
                result = {"selected": selected or None}
            else:
                self._json({"error": "unknown_endpoint"}, HTTPStatus.NOT_FOUND); return
            self._json(result)
        except Exception as exc:
            self._json({"error": type(exc).__name__, "message": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Bookflow UI is local-only")
    if not ROOT.is_dir():
        raise RuntimeError(f"UI resources are missing: {ROOT}")
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    print(json.dumps({"url": url, "status": "running"}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
