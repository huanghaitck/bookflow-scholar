from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from bookflow.batch_backend import BatchBackend
from bookflow.provider_registry import (
    ProviderSchemaError,
    RegistryTranslationProvider,
    normalize_translation_result,
)


def _pdf(path: Path, pages: int, marker: str) -> Path:
    document = fitz.open()
    for page_number in range(1, pages + 1):
        page = document.new_page()
        page.insert_text((50, 70), f"{marker} page {page_number} of {pages}")
    document.save(path)
    document.close()
    return path


def _wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def _quick_result(root: Path, job: dict) -> dict:
    output = root / job["project_id"] / job["workspace_id"]
    output.mkdir(parents=True, exist_ok=True)
    return {"output_path": str(output), "request_count": 0, "usage": {}}


def test_three_projects_isolate_active_context_jobs_events_and_workspace_ids(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    source = _pdf(tmp_path / "shared.pdf", 2, "shared immutable input")
    project_ids: list[str] = []
    batch_ids: list[str] = []
    workspace_ids: list[str] = []
    targets = ["en", "fr", "ja"]

    for index, target in enumerate(targets):
        project = backend.create_project(f"Project {index}")
        imported = backend.import_sources(
            project["project_id"], [source], command_id=f"import-{index}",
            source_languages={str(source): "de"},
            pipeline_config={"source_language": "de", "target_language": target},
        )
        snapshot = backend.snapshot(project_id=project["project_id"])
        project_ids.append(project["project_id"])
        batch_ids.append(imported["batch_id"])
        workspace_ids.append(snapshot["jobs"][0]["workspace_id"])
        assert {job["project_id"] for job in snapshot["jobs"]} == {project["project_id"]}
        assert {event["project_id"] for event in snapshot["recent_events"]} <= {project["project_id"]}

    assert len(set(workspace_ids)) == 3
    assert len({backend.snapshot(project_id=project_id)["jobs"][0]["batch_id"] for project_id in project_ids}) == 3

    for project_id, batch_id, workspace_id in zip(project_ids, batch_ids, workspace_ids):
        backend.open_project(project_id)
        snapshot = backend.snapshot()
        assert snapshot["active_context"]["active_project_id"] == project_id
        assert snapshot["active_context"]["active_batch_id"] == batch_id
        assert snapshot["active_context"]["workspace_id"] == workspace_id
        assert {job["project_id"] for job in snapshot["jobs"]} == {project_id}


def test_runtime_page_counts_special_filenames_and_project_switching(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    dynamic_n = random.SystemRandom().randint(4, 12)
    counts = [1, 2, 3, 7, 28, dynamic_n]
    project = backend.create_project("Runtime page matrix")
    sources = [_pdf(tmp_path / f"runtime-{index}.pdf", count, f"runtime {index}") for index, count in enumerate(counts)]
    result = backend.import_sources(project["project_id"], sources, command_id="page-matrix")
    assert result["imported"] == len(sources)
    snapshot = backend.snapshot(project_id=project["project_id"])
    observed = {source["filename"]: source for source in snapshot["sources"]}
    for path, expected in zip(sources, counts):
        assert observed[path.name]["page_count"] == expected
        assert len(observed[path.name]["thumbnails"]) == expected
        assert observed[path.name]["thumbnails"][-1]["page"] == expected

    names = [
        "ordinary.pdf", "中文 文件名.pdf", "name,with,commas.pdf",
        "name with spaces.pdf", "name's quoted.pdf", "日本語テスト.pdf",
    ]
    special_project = backend.create_project("Special filenames")
    special = [_pdf(tmp_path / name, 1, f"special {index}") for index, name in enumerate(names)]
    imported = backend.import_sources(special_project["project_id"], special, command_id="special-filenames")
    assert imported["imported"] == len(names)
    assert {source["filename"] for source in backend.snapshot(project_id=special_project["project_id"])["sources"]} == set(names)

    switch_projects: dict[int, str] = {}
    for count in (2, 7, 28):
        switch_project = backend.create_project(f"Switch {count}")
        switch_source = _pdf(tmp_path / f"switch-{count}.pdf", count, f"switch {count}")
        backend.import_sources(switch_project["project_id"], [switch_source], command_id=f"switch-{count}")
        switch_projects[count] = switch_project["project_id"]
    for count in (2, 28, 7, 2):
        backend.open_project(switch_projects[count])
        active = backend.snapshot()
        assert active["active_project"]["project_id"] == switch_projects[count]
        assert [source["page_count"] for source in active["sources"]] == [count]
        assert len(active["sources"][0]["thumbnails"]) == count
        assert {job["project_id"] for job in active["jobs"]} == {switch_projects[count]}


def test_provider_alias_is_validated_and_attempt_ledger_is_redacted(tmp_path: Path) -> None:
    canonical = normalize_translation_result(
        {"translation_text": " translated "}, source_language="en", target_language="fr"
    )
    assert canonical.translated_text == "translated"
    with pytest.raises(ProviderSchemaError, match="translated_text"):
        normalize_translation_result({}, source_language="en", target_language="fr")

    raw = {
        "id": "request-1",
        "choices": [{"message": {"content": json.dumps({"translation_text": "bonjour"})}}],
    }

    class Client:
        profile = SimpleNamespace(provider_id="compatible-language", model="language-model", extra={})
        last_request_metrics = {
            "start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T00:00:01+00:00",
            "latency": 1.0, "retry_number": 0, "usage": {"input_tokens": 2}, "status": "succeeded",
        }

        def text_json(self, **_kwargs):
            return raw

    provider = RegistryTranslationProvider(tmp_path / "workspace", Client())
    source_text = "private source body"
    unit = {
        "translation_unit_id": "segment-1", "source_text": source_text,
        "source_text_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "source_language": "en", "target_language": "fr", "project_id": "project-dynamic", "job_id": "job-dynamic",
    }
    assert provider.translate_batch([unit])[0]["translated_text"] == "bonjour"
    ledger_text = (tmp_path / "workspace/logs/text_attempts.jsonl").read_text("utf-8")
    ledger = [json.loads(line) for line in ledger_text.splitlines()]
    assert source_text not in ledger_text
    assert ledger[-1]["response_hash"] == hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    assert {"attempt_id", "project_id", "job_id", "stage", "provider_role", "provider", "model",
            "page_or_segment_id", "purpose", "start", "end", "latency", "status", "retry_number",
            "usage", "request_hash", "response_hash", "error_code"} <= set(ledger[-1])


def test_retry_recover_and_cancel_start_or_stop_the_owning_worker(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend", background_worker=True)
    attempts: dict[str, int] = {}

    def flaky(job: dict) -> dict:
        attempts[job["job_id"]] = attempts.get(job["job_id"], 0) + 1
        if attempts[job["job_id"]] == 1:
            raise RuntimeError("first attempt fails")
        return _quick_result(tmp_path / "outputs", job)

    backend._process_job = flaky  # type: ignore[method-assign]
    project = backend.create_project("Retry")
    batch = backend.import_sources(project["project_id"], [_pdf(tmp_path / "retry.pdf", 1, "retry")], command_id="retry")["batch_id"]
    started = backend.start_batch(batch)
    assert started["worker_session_id"]
    _wait_until(lambda: backend.snapshot(batch_id=batch)["jobs"][0]["state"] == "failed")
    job_id = backend.snapshot(batch_id=batch)["jobs"][0]["job_id"]
    retried = backend.retry_job(job_id)
    assert retried["worker_session_id"] and retried["worker"] in {"started", "existing"}
    _wait_until(lambda: backend.snapshot(batch_id=batch)["jobs"][0]["state"] == "completed")

    backend._process_job = lambda job: _quick_result(tmp_path / "outputs", job)  # type: ignore[method-assign]
    recover_project = backend.create_project("Recover")
    recover_batch = backend.import_sources(
        recover_project["project_id"], [_pdf(tmp_path / "recover.pdf", 1, "recover")], command_id="recover"
    )["batch_id"]
    with sqlite3.connect(backend.database_path) as connection:
        connection.execute("UPDATE jobs SET state='paused' WHERE batch_id=?", (recover_batch,))
        connection.execute("UPDATE batches SET state='paused',pause_requested=1 WHERE batch_id=?", (recover_batch,))
    recovered = backend.recover_batch(recover_batch)
    assert recovered["worker_session_id"] and recovered["worker"] in {"started", "existing"}
    _wait_until(lambda: backend.snapshot(batch_id=recover_batch)["jobs"][0]["state"] == "completed")

    entered = threading.Event()
    release = threading.Event()

    def blocking(job: dict) -> dict:
        entered.set()
        assert release.wait(10)
        return _quick_result(tmp_path / "outputs", job)

    backend._process_job = blocking  # type: ignore[method-assign]
    cancel_project = backend.create_project("Cancel")
    cancel_batch = backend.import_sources(
        cancel_project["project_id"], [_pdf(tmp_path / "cancel.pdf", 1, "cancel")], command_id="cancel"
    )["batch_id"]
    owner = backend.start_batch(cancel_batch)
    assert owner["worker_session_id"] and entered.wait(10)
    cancel_job = backend.snapshot(batch_id=cancel_batch)["jobs"][0]["job_id"]
    assert backend.cancel_job(cancel_job)["changed"] is True
    release.set()
    _wait_until(lambda: not backend.worker_registry.live())
    assert backend.snapshot(batch_id=cancel_batch)["jobs"][0]["state"] == "cancelled"
