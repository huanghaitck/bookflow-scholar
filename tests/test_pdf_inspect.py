from __future__ import annotations

import hashlib
import shutil
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

import bookflow.pdf_inspect as pdf_module
from bookflow.cli import app
from bookflow.paths import project_root
from bookflow.pdf_inspect import inspect_pdf


SAMPLE = project_root() / "input" / "sample_11_pages.pdf"
FULL = project_root() / "input" / "The big game of central and western China (1913).pdf"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_sample_actual_page_count_is_11():
    result = inspect_pdf(SAMPLE)
    assert result.page_count == 11
    assert result.has_text_layer is True
    assert len(result.per_page_text_characters or []) == 11


def test_misleading_sample_10_pages_filename_does_not_control_page_count(tmp_path):
    misleading = tmp_path / "sample_10_pages.pdf"
    shutil.copy2(SAMPLE, misleading)
    result = inspect_pdf(misleading)
    assert result.filename == "sample_10_pages.pdf"
    assert result.page_count == 11


def test_pdf_path_with_spaces_and_parentheses(tmp_path):
    copy_path = tmp_path / "A sample book (1913).pdf"
    shutil.copy2(SAMPLE, copy_path)
    assert inspect_pdf(copy_path).page_count == 11


def test_inspection_does_not_modify_pdf():
    before_hash = _sha256(SAMPLE)
    before_stat = SAMPLE.stat()
    inspect_pdf(SAMPLE)
    after_stat = SAMPLE.stat()
    assert _sha256(SAMPLE) == before_hash
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns


def test_missing_pdf_has_clear_error(tmp_path):
    missing = tmp_path / "missing.pdf"
    with pytest.raises(FileNotFoundError, match="PDF file not found"):
        inspect_pdf(missing)


def test_cli_missing_pdf_returns_clear_error(tmp_path):
    missing = tmp_path / "missing.pdf"
    result = CliRunner().invoke(app, ["inspect-pdf", str(missing)])
    assert result.exit_code == 2
    assert "PDF file not found" in result.output


def test_full_pdf_metadata_only_never_extracts_page_text(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("full PDF page text was accessed")

    monkeypatch.setattr(pdf_module, "_page_text_char_count", forbidden)
    result = inspect_pdf(FULL, analyze_text_layer=False)
    assert result.page_count == 412
    assert result.metadata_only is True
    assert result.per_page_text_characters is None


def test_pdf_inspection_does_not_use_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert inspect_pdf(SAMPLE).page_count == 11


def test_invalid_metadata_replacement_character_is_sanitized(monkeypatch):
    class FakeDocument:
        page_count = 1
        metadata = {"producer": "bad\ufffdmetadata"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def __iter__(self):
            return iter(())

    monkeypatch.setattr(pdf_module.fitz, "open", lambda path: FakeDocument())
    result = inspect_pdf(SAMPLE, analyze_text_layer=False)
    assert result.metadata["producer"] == "bad?metadata"
