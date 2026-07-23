"""JSON transport for the Tauri-owned persistent Python sidecar."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .batch_backend import BatchBackend


def _transport_command(backend: BatchBackend, envelope: dict[str, Any]) -> dict[str, Any] | None:
    command = envelope["command"]
    payload = envelope.get("payload") or {}
    path: Path | None = None
    result: dict[str, Any] | None = None
    if command == "openWebAssistPackageFolder":
        package = backend.web_assist.get_package(str(payload["package_id"]))
        path = Path(package["export_path"])
        result = {"package_id": package["package_id"], "opened_path": str(path)}
    if path is None or result is None:
        return None
    if os.name != "nt":
        raise RuntimeError("desktop resource reveal is currently available on Windows only")
    os.startfile(path)  # type: ignore[attr-defined]
    return backend._response(str(envelope["command_id"]), command, True, result=result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", required=True, type=Path)
    parser.add_argument("--provider-config", required=True, type=Path)
    parser.add_argument("--after-sequence", type=int, default=0)
    parser.add_argument("--persistent", action="store_true")
    args = parser.parse_args()
    backend = BatchBackend(
        args.backend_root,
        provider_config_path=args.provider_config,
        background_worker=args.persistent,
    )
    if args.persistent:
        return _persistent_main(backend, after_sequence=args.after_sequence)
    envelope = json.loads(sys.stdin.read().lstrip("\ufeff"))
    response = _transport_command(backend, envelope)
    if response is None:
        response = backend.execute(
            str(envelope["command"]),
            dict(envelope.get("payload") or {}),
            command_id=str(envelope["command_id"]),
            schema_version=str(envelope["schema_version"]),
        )
    events = backend.events(after_sequence=args.after_sequence)
    last_sequence = max((int(item["sequence"]) for item in events), default=args.after_sequence)
    sys.stdout.write(json.dumps({"response": response, "events": events, "last_sequence": last_sequence}, ensure_ascii=False))
    return 0


def _persistent_main(backend: BatchBackend, *, after_sequence: int = 0) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    write_lock = threading.Lock()
    stopped = threading.Event()
    sequence = after_sequence

    def emit(value: dict[str, Any]) -> None:
        with write_lock:
            sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def event_pump() -> None:
        nonlocal sequence
        last_heartbeat = 0.0
        while not stopped.wait(0.1):
            for event in backend.events(after_sequence=sequence):
                sequence = max(sequence, int(event["sequence"]))
                emit({"kind": "event", "event": event, "last_sequence": sequence})
            now = time.monotonic()
            if now - last_heartbeat >= 1.0:
                emit({"kind": "heartbeat", "timestamp": time.time(), "last_sequence": sequence,
                      "backend_state": "running"})
                last_heartbeat = now

    emit({"kind": "ready", "protocol_version": "bookflow-sidecar-v1", "backend_pid": os.getpid(),
          "schema_version": "1.2", "capabilities": backend.capabilities(),
          "last_sequence": sequence})
    pump = threading.Thread(target=event_pump, name="bookflow-event-pump", daemon=True)
    pump.start()
    try:
        for raw in sys.stdin:
            if not raw.strip():
                continue
            frame = json.loads(raw.lstrip("\ufeff"))
            kind = str(frame.get("kind") or "command")
            request_id = str(frame.get("request_id") or "")
            if kind == "shutdown":
                stopped.set()
                result = backend.shutdown(timeout=10.0)
                emit({"kind": "stopped", "request_id": request_id, "last_sequence": sequence, **result})
                return 0
            if kind == "ping":
                emit({"kind": "heartbeat", "request_id": request_id, "timestamp": time.time(),
                      "last_sequence": sequence, "backend_state": "running"})
                continue
            envelope = dict(frame.get("envelope") or frame)
            try:
                response = _transport_command(backend, envelope)
                if response is None:
                    response = backend.execute(str(envelope["command"]), dict(envelope.get("payload") or {}),
                                               command_id=str(envelope["command_id"]),
                                               schema_version=str(envelope["schema_version"]))
                emit({"kind": "response", "request_id": request_id or str(envelope["command_id"]),
                      "response": response})
            except Exception as exc:
                emit({"kind": "response", "request_id": request_id,
                      "response": backend._response(request_id, str(envelope.get("command") or "unknown"), False,
                                                     error={"error_code": "sidecar_command_failed",
                                                            "user_message": str(exc)[:500],
                                                            "recoverable": True})})
    finally:
        stopped.set()
        backend.shutdown(timeout=10.0)
        pump.join(2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
