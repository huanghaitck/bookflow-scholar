from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bookflow.io_utils import sha256_file
from bookflow.paths import project_root
from bookflow.phase5_audit import release_phase5_1_clean


ROOT = project_root()


def _release_fixture(tmp_path: Path) -> Path:
    files = (
        "data/source_document_sample12_v1.json",
        "data/bilingual_document_sample12_zh-Hans_v1.json",
        "output/candidate/source_english_sample12.md",
        "output/candidate/source_english_sample12.docx",
        "output/candidate/bilingual_zh-Hans_sample12.md",
        "output/candidate/bilingual_zh-Hans_sample12.docx",
        "output/audit/phase5/converted/source_english_sample12.pdf",
        "output/audit/phase5/converted/bilingual_zh-Hans_sample12.pdf",
        "output/final/source_english.md",
        "output/final/source_english.docx",
        "config/settings.example.yaml",
        "language_profiles/zh-Hans.yaml",
        "prompts/translation_en_zh_v2.md",
    )
    for relative in files:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def test_clean_release_archives_old_english_final_and_publishes_four_files(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    old_md_hash = sha256_file(root / "output/final/source_english.md")
    old_docx_hash = sha256_file(root / "output/final/source_english.docx")

    result = release_phase5_1_clean(root=root, test_count=212)

    assert result.published is True
    assert sha256_file(root / "output/archive/pre_phase5_clean_release/source_english.md") == old_md_hash
    assert sha256_file(root / "output/archive/pre_phase5_clean_release/source_english.docx") == old_docx_hash
    assert sha256_file(root / "output/final/source_english.md") == sha256_file(
        root / "output/candidate/source_english_sample12.md"
    )
    assert sha256_file(root / "output/final/source_english.docx") == sha256_file(
        root / "output/candidate/source_english_sample12.docx"
    )
    assert sha256_file(root / "output/final/bilingual_zh-Hans.md") == sha256_file(
        root / "output/candidate/bilingual_zh-Hans_sample12.md"
    )
    assert sha256_file(root / "output/final/bilingual_zh-Hans.docx") == sha256_file(
        root / "output/candidate/bilingual_zh-Hans_sample12.docx"
    )


def test_clean_release_preserves_canonical_inputs(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    protected = [
        root / "data/source_document_sample12_v1.json",
        root / "data/bilingual_document_sample12_zh-Hans_v1.json",
        *sorted((root / "output/candidate").iterdir()),
    ]
    before = {str(path): sha256_file(path) for path in protected}

    release_phase5_1_clean(root=root, test_count=212)

    assert {str(path): sha256_file(path) for path in protected} == before


def test_clean_release_manifest_records_zero_phase51_api_calls(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    result = release_phase5_1_clean(root=root, test_count=212)
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))

    assert manifest["api_calls"] == {
        "glm": 0,
        "deepseek": 0,
        "translation": 0,
        "network_requests": 0,
    }
    assert manifest["release_mode"] == "clean_reader_edition"
    assert manifest["archive_paths"]


def test_clean_release_refuses_to_overwrite_conflicting_archive(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    archive = root / "output/archive/pre_phase5_clean_release/source_english.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("conflicting archive", encoding="utf-8")

    with pytest.raises(RuntimeError, match="archive conflict"):
        release_phase5_1_clean(root=root, test_count=212)

    assert not (root / "output/final/bilingual_zh-Hans.md").exists()


def test_clean_release_final_contains_no_internal_reader_noise(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    release_phase5_1_clean(root=root, test_count=212)
    source = (root / "output/final/source_english.md").read_text(encoding="utf-8")

    assert "canonical_source_json_sha256" not in source
    assert "[Source pages:" not in source
    assert "logical2_" not in source
