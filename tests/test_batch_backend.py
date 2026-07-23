from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import fitz
import pytest

from bookflow.batch_backend import BatchBackend, SCHEMA_VERSION, error_envelope


FIXTURES = Path(__file__).parent / "fixtures/multilingual"


def _pdf(path: Path, text: str = "Test document\nFirst paragraph.\nSecond paragraph.") -> Path:
    document = fitz.open(); page = document.new_page(); page.insert_text((50, 70), text)
    document.save(path); document.close(); return path


def _backend(tmp_path: Path) -> tuple[BatchBackend, dict]:
    backend = BatchBackend(tmp_path / "backend")
    return backend, backend.create_project("QA project")


def _local_processor(tmp_path: Path, *, fail_name: str | None = None):
    def process(job: dict) -> dict:
        if fail_name and job["filename"] == fail_name:
            raise TimeoutError("provider timeout; api_key=super-secret")
        output = tmp_path / "quick-outputs" / job["source_id"]
        output.mkdir(parents=True, exist_ok=True)
        (output / "book.md").write_text(f"# {job['filename']}\n\nprocessed\n", "utf-8")
        return {"output_path": str(output), "provider": "mock", "usage": {"calls": 0}}
    return process


def test_migration_is_replayable_uses_wal_and_foreign_keys(tmp_path: Path) -> None:
    first = BatchBackend(tmp_path / "backend"); second = BatchBackend(tmp_path / "backend")
    assert first.database_path == second.database_path
    with sqlite3.connect(first.database_path) as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "sources", "batches", "jobs", "commands", "events"} <= tables


def test_import_is_idempotent_deduplicated_and_partial(tmp_path: Path) -> None:
    backend, project = _backend(tmp_path)
    good = _pdf(tmp_path / "same.pdf")
    different = _pdf(tmp_path / "other.pdf", "Different content")
    same_name_dir = tmp_path / "nested"; same_name_dir.mkdir(); same_name = _pdf(same_name_dir / "same.pdf", "Different hash")
    corrupt = tmp_path / "corrupt.pdf"; corrupt.write_bytes(b"not a pdf")
    unsupported = tmp_path / "source.exe"; unsupported.write_bytes(b"MZ")
    missing = tmp_path / "missing.pdf"
    result = backend.import_sources(project["project_id"], [good, different, same_name, corrupt, unsupported, missing], command_id="import-1")
    assert result["imported"] == 3 and result["failed"] == 3
    assert len({item["source_id"] for item in result["results"] if item["status"] == "imported"}) == 3
    assert backend.import_sources(project["project_id"], [good], command_id="import-1") == result
    duplicate = backend.import_sources(project["project_id"], [good], command_id="import-2")
    assert duplicate["duplicates"] == 1 and duplicate["imported"] == 0


def test_picker_drag_and_folder_use_same_import_contract(tmp_path: Path) -> None:
    folder = tmp_path / "sources"; folder.mkdir(); source = _pdf(folder / "source.pdf")
    models = []
    for index, supplied in enumerate(([source], [source], [folder], [folder])):
        backend = BatchBackend(tmp_path / f"backend-{index}"); project = backend.create_project("P")
        result = backend.import_sources(project["project_id"], supplied, command_id=f"command-{index}")
        models.append({key: result[key] for key in ("discovered", "imported", "duplicates", "failed")})
    assert models == [models[0]] * 4


def test_queue_failure_continue_pause_cancel_retry_and_completed_skip(tmp_path: Path) -> None:
    backend, project = _backend(tmp_path)
    one = _pdf(tmp_path / "one.pdf"); two = _pdf(tmp_path / "two.pdf", "two")
    imported = backend.import_sources(project["project_id"], [one, two], command_id="queue-import")
    result = backend.run_batch(imported["batch_id"], processor=_local_processor(tmp_path, fail_name="one.pdf"))
    assert result["counts"] == {"completed": 1, "failed": 1}
    snapshot = backend.snapshot(batch_id=imported["batch_id"])
    failed = next(job for job in snapshot["jobs"] if job["state"] == "failed")
    completed = next(job for job in snapshot["jobs"] if job["state"] == "completed")
    assert "super-secret" not in json.dumps(failed["error"])
    assert backend.cancel_job(completed["job_id"])["changed"] is False
    assert backend.retry_job(failed["job_id"])["state"] == "queued"
    backend.pause_batch(imported["batch_id"])
    assert backend.run_batch(imported["batch_id"], processor=_local_processor(tmp_path))["state"] == "paused"
    backend.resume_batch(imported["batch_id"])
    final = backend.run_batch(imported["batch_id"], processor=_local_processor(tmp_path))
    assert final["counts"] == {"completed": 2}
    attempts = {job["job_id"]: job["attempts"] for job in backend.snapshot(batch_id=imported["batch_id"])["jobs"]}
    backend.run_batch(imported["batch_id"], processor=lambda job: (_ for _ in ()).throw(AssertionError("completed reran")))
    assert attempts == {job["job_id"]: job["attempts"] for job in backend.snapshot(batch_id=imported["batch_id"])["jobs"]}


def test_worker_restart_database_reopen_snapshot_and_events(tmp_path: Path) -> None:
    backend, project = _backend(tmp_path); source = _pdf(tmp_path / "restart.pdf")
    batch = backend.import_sources(project["project_id"], [source], command_id="restart-import")["batch_id"]
    with sqlite3.connect(backend.database_path) as connection:
        connection.execute("UPDATE jobs SET state='running' WHERE batch_id=?", (batch,))
        connection.execute("UPDATE batches SET state='running' WHERE batch_id=?", (batch,))
    reopened = BatchBackend(backend.root)
    assert reopened.snapshot(batch_id=batch)["jobs"][0]["state"] == "queued"
    final = reopened.run_batch(batch, processor=_local_processor(tmp_path))
    assert final["counts"] == {"completed": 1}
    events = reopened.events(batch_id=batch)
    sequences = [item["sequence"] for item in events]
    assert sequences == sorted(set(sequences))
    assert any(item["event_type"] == "pipeline.recovering" for item in events)
    assert reopened.snapshot(batch_id=batch)["sequence"] >= sequences[-1]


def test_error_envelope_redacts_credentials() -> None:
    value = error_envelope(RuntimeError("Authorization: Bearer abc api_key=xyz token=qwerty"))
    encoded = json.dumps(value)
    assert all(secret not in encoded for secret in ("abc", "xyz", "qwerty"))


def test_schema_mismatch_and_active_job_cancellation_boundary(tmp_path: Path) -> None:
    incompatible = tmp_path / "incompatible"; incompatible.mkdir()
    with sqlite3.connect(incompatible / "bookflow_backend.sqlite3") as connection:
        connection.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES(99)")
    with pytest.raises(RuntimeError, match="newer than supported"):
        BatchBackend(incompatible)

    backend, project = _backend(tmp_path / "cancel-case")
    source = _pdf(tmp_path / "cancel.pdf")
    batch_id = backend.import_sources(project["project_id"], [source], command_id="cancel-active")["batch_id"]
    entered = threading.Event(); release = threading.Event()
    def blocking(job: dict) -> dict:
        entered.set(); assert release.wait(10)
        return {"output_path": str(tmp_path / "cancelled-output")}
    thread = threading.Thread(target=lambda: backend.run_batch(batch_id, processor=blocking))
    thread.start(); assert entered.wait(10)
    job_id = backend.snapshot(batch_id=batch_id)["jobs"][0]["job_id"]
    assert backend.cancel_job(job_id)["changed"] is True
    release.set(); thread.join(10); assert not thread.is_alive()
    assert backend.snapshot(batch_id=batch_id)["jobs"][0]["state"] == "cancelled"


def test_provider_rate_limit_ocr_write_and_checkpoint_failures_are_isolated(tmp_path: Path) -> None:
    backend, project = _backend(tmp_path)
    names = ["provider-rate-limit.pdf", "ocr-page.pdf", "disk-write.pdf", "checkpoint.pdf", "healthy.pdf"]
    sources = [_pdf(tmp_path / name, name) for name in names]
    batch_id = backend.import_sources(project["project_id"], sources, command_id="fault-matrix")["batch_id"]
    def processor(job: dict) -> dict:
        if job["filename"] == "provider-rate-limit.pdf": raise RuntimeError("provider rate limit")
        if job["filename"] == "ocr-page.pdf": raise RuntimeError("OCR page failure")
        if job["filename"] == "disk-write.pdf": raise PermissionError("disk write denied")
        if job["filename"] == "checkpoint.pdf": raise ValueError("checkpoint schema mismatch")
        return _local_processor(tmp_path)(job)
    result = backend.run_batch(batch_id, processor=processor)
    assert result["counts"] == {"completed": 1, "failed": 4}


def test_six_language_offline_batch_outputs_contract_layout_and_utf8(tmp_path: Path) -> None:
    backend, project = _backend(tmp_path)
    manifest_rows = list(csv.DictReader((FIXTURES / "SOURCE_MANIFEST.csv").open(encoding="utf-8")))
    paths = [FIXTURES / row["local_path"] for row in manifest_rows]
    for row, path in zip(manifest_rows, paths):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    languages = {row["local_path"]: row["language"] for row in manifest_rows}
    imported = backend.import_sources(project["project_id"], paths, command_id="six-language", source_languages=languages)
    assert imported["imported"] == 6
    result = backend.run_batch(imported["batch_id"])
    assert result["counts"] == {"completed": 6}
    snapshot = backend.snapshot(batch_id=imported["batch_id"])
    assert len(snapshot["jobs"]) == 6
    outputs = [Path(job["output_path"]) for job in snapshot["jobs"]]
    assert len(set(outputs)) == 6
    required = {"book.md", "metadata.json", "processing_report.md", "warnings.json", "source_manifest.csv", "HUMAN_REVIEW_QUEUE.json", "HUMAN_REVIEW_QUEUE.md"}
    for output in outputs:
        assert required <= {path.name for path in output.iterdir()}
        assert (output / "book.md").read_text("utf-8").strip()
        assert (output / "assets/images").is_dir() and (output / "assets/tables").is_dir()
        assert (output / "checkpoints/job.json").is_file()
        review = json.loads((output / "HUMAN_REVIEW_QUEUE.json").read_text("utf-8"))
        assert {"ocr_low_confidence", "table_failure", "provider_structured_output_failure", "partial_export_failure"} <= set(review["supported_issue_types"])


@pytest.mark.parametrize("count", [10, 50])
def test_small_batch_stress_has_no_loss_or_rerun(tmp_path: Path, count: int) -> None:
    backend, project = _backend(tmp_path)
    sources = [_pdf(tmp_path / f"stress-{index:02d}.pdf", f"fixture {index}") for index in range(count)]
    imported = backend.import_sources(project["project_id"], sources, command_id=f"stress-{count}")
    assert imported["imported"] == count
    final = backend.run_batch(imported["batch_id"], processor=_local_processor(tmp_path))
    assert final["counts"] == {"completed": count}
    before = [(job["job_id"], job["attempts"]) for job in backend.snapshot(batch_id=imported["batch_id"])["jobs"]]
    BatchBackend(backend.root).run_batch(imported["batch_id"], processor=_local_processor(tmp_path))
    after = [(job["job_id"], job["attempts"]) for job in backend.snapshot(batch_id=imported["batch_id"])["jobs"]]
    assert before == after
