from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import fitz
import pytest
from PIL import Image

import bookflow.page_pipeline as page_module
from bookflow.io_utils import load_json, sha256_file
from bookflow.mock_vision import run_mock_vision
from bookflow.page_pipeline import (
    build_context,
    page_status,
    render_pages,
    validate_manifest_file,
)
from bookflow.paths import ProjectSettings, project_root
from bookflow.schemas import ContinuityCandidate, PageManifest, PageRecord, VisionPageResult


SAMPLE = project_root() / "input" / "sample_11_pages.pdf"
FULL = project_root() / "input" / "The big game of central and western China (1913).pdf"


def _settings(root: Path, *, sample: Path = SAMPLE, source: Path = FULL) -> ProjectSettings:
    return ProjectSettings.model_validate(
        {
            "source_pdf": str(source),
            "sample_pdf": str(sample),
            "output_directory": str(root / "output"),
            "cache_directory": str(root / "cache"),
            "page_image_directory": str(root / "pages"),
            "manifest_directory": str(root / "manifests"),
            "mock_vision_directory": str(root / "mock"),
            "continuity_directory": str(root / "continuity"),
            "log_directory": str(root / "logs"),
            "temporary_directory": str(root / "tmp"),
            "vision_provider": "configurable-vision",
            "vision_base_url": "https://example.invalid/v1",
            "vision_model": "configurable-model",
            "translation_provider": "configurable-translation",
            "translation_base_url": "https://example.invalid/v1",
            "translation_model": "configurable-translation-model",
            "maximum_cash_cost_cny": 2.0,
            "default_page_range": [1, 11],
            "sample_page_range": [1, 11],
            "dry_run": True,
        }
    )


def _render(root: Path, *, pages="1-11", dpi=72, **kwargs):
    settings = _settings(root)
    return render_pages(
        SAMPLE,
        settings,
        pages=pages,
        dpi=dpi,
        root=root,
        **kwargs,
    )


@pytest.fixture(scope="module")
def rendered_sample(tmp_path_factory):
    root = tmp_path_factory.mktemp("phase1b-rendered")
    settings = _settings(root)
    result = render_pages(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    return root, settings, result


def test_sample_renders_actual_11_pages(rendered_sample):
    _, _, result = rendered_sample
    assert result.actual_page_count == 11
    assert result.rendered_pages == list(range(1, 12))
    assert not result.failed_pages


def test_all_rendered_images_open_with_pillow_and_are_continuous(rendered_sample):
    _, _, result = rendered_sample
    image_dir = Path(result.output_directory)
    images = sorted(image_dir.glob("page_*.png"))
    assert [path.name for path in images] == [f"page_{page:04d}.png" for page in range(1, 12)]
    for path in images:
        with Image.open(path) as image:
            assert image.width > 0
            assert image.height > 0
            image.verify()


def test_rendering_does_not_change_source_pdf(rendered_sample):
    before = "c66d2d4a1eb143e08ef580dcd21701db3e30ea041c28877e20d61b5687524d51"
    assert sha256_file(SAMPLE) == before
    _, _, result = rendered_sample
    manifest = PageManifest.model_validate(load_json(result.manifest_path))
    assert manifest.source_pdf_sha256 == before


def test_output_image_hash_is_stable_when_forced(tmp_path):
    first = _render(tmp_path, pages="1", dpi=72)
    image = Path(first.output_directory) / "page_0001.png"
    first_hash = sha256_file(image)
    second = _render(tmp_path, pages="1", dpi=72, force=True)
    assert second.rendered_pages == [1]
    assert sha256_file(image) == first_hash


def test_second_run_hits_cache_and_does_not_rewrite_image(tmp_path):
    first = _render(tmp_path, pages="1-3", dpi=72)
    image = Path(first.output_directory) / "page_0001.png"
    before_mtime = image.stat().st_mtime_ns
    second = _render(tmp_path, pages="1-3", dpi=72)
    assert second.rendered_pages == []
    assert second.cached_pages == [1, 2, 3]
    assert image.stat().st_mtime_ns == before_mtime


def test_different_dpi_uses_a_different_cache_profile(tmp_path):
    first = _render(tmp_path, pages="1", dpi=72)
    second = _render(tmp_path, pages="1", dpi=96)
    assert first.output_directory != second.output_directory
    assert first.cache_index_path != second.cache_index_path
    assert second.rendered_pages == [1]


def test_one_page_failure_does_not_damage_other_pages(tmp_path):
    def fail_page_two(page, destination, dpi, color_mode):
        if page.number == 1:
            raise RuntimeError("simulated page failure")
        return page_module._render_page_image(page, destination, dpi, color_mode)

    result = _render(
        tmp_path,
        pages="1-3",
        dpi=72,
        render_page_func=fail_page_two,
    )
    assert result.rendered_pages == [1, 3]
    assert result.failed_pages == [2]
    assert (Path(result.output_directory) / "page_0001.png").is_file()
    assert (Path(result.output_directory) / "page_0003.png").is_file()


def test_interrupted_run_resumes_only_unfinished_pages(tmp_path):
    first = _render(tmp_path, pages="1-11", dpi=72, max_pages_this_run=3)
    assert first.rendered_pages == [1, 2, 3]
    assert first.skipped_pages == list(range(4, 12))
    second = _render(tmp_path, pages="1-11", dpi=72)
    assert second.cached_pages == [1, 2, 3]
    assert second.rendered_pages == list(range(4, 12))


def test_orphan_image_without_manifest_is_reported(tmp_path):
    result = _render(tmp_path, pages="1", dpi=72)
    context = build_context(SAMPLE, _settings(tmp_path), pages="1", dpi=72, root=tmp_path)
    record = Path(context.record_directory) / "page_0001.json"
    record.unlink()
    Path(result.manifest_path).unlink()
    status = page_status(SAMPLE, _settings(tmp_path), pages="1", dpi=72, root=tmp_path)
    assert status.orphan_image_pages == [1]
    assert status.manifest_exists is False
    assert status.ready_for_vision is False


def test_image_hash_mismatch_is_reported(tmp_path):
    result = _render(tmp_path, pages="1", dpi=72)
    image = Path(result.output_directory) / "page_0001.png"
    with image.open("ab") as handle:
        handle.write(b"tamper")
    status = page_status(SAMPLE, _settings(tmp_path), pages="1", dpi=72, root=tmp_path)
    assert status.hash_anomaly_pages == [1]
    assert status.ready_for_vision is False


def test_mock_provider_is_offline_and_non_authoritative(rendered_sample, monkeypatch):
    root, settings, _ = rendered_sample

    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = run_mock_vision(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    assert result.api_called is False
    assert result.authoritative is False
    assert result.provider == "mock"
    assert not result.failed_pages


def test_mock_outputs_do_not_claim_glm_or_ocr(rendered_sample):
    root, settings, render_result = rendered_sample
    run_mock_vision(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    context = build_context(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    normalized_path = (
        Path(settings.mock_vision_directory)
        / context.document_slug
        / f"profile_{context.render_profile_id}"
        / "normalized"
        / "page_0001.json"
    )
    normalized = VisionPageResult.model_validate(load_json(normalized_path))
    assert normalized.provider == "mock"
    assert normalized.model is None
    assert normalized.source_method == "pdf_text_layer"
    assert normalized.authoritative is False
    assert normalized.api_called is False
    assert Path(normalized.raw_response_path).parent.name == "raw"
    assert Path(normalized.normalized_output_path).parent.name == "normalized"
    assert render_result.document_id == normalized.document_id


def test_empty_text_layer_does_not_invent_body_text(tmp_path):
    blank = tmp_path / "Blank sample (test).pdf"
    document = fitz.open()
    document.new_page()
    document.save(blank)
    document.close()
    settings = _settings(tmp_path, sample=blank)
    settings.sample_page_range = [1, 1]
    render_pages(blank, settings, pages="1", dpi=72, root=tmp_path)
    result = run_mock_vision(blank, settings, pages="1", dpi=72, root=tmp_path)
    context = build_context(blank, settings, pages="1", dpi=72, root=tmp_path)
    normalized_path = (
        Path(settings.mock_vision_directory)
        / context.document_slug
        / f"profile_{context.render_profile_id}"
        / "normalized"
        / "page_0001.json"
    )
    normalized = VisionPageResult.model_validate(load_json(normalized_path))
    assert result.needs_real_vision_pages == [1]
    assert normalized.status == "needs_real_vision"
    assert normalized.blocks == []


def test_continuity_records_are_pending_and_never_merge_text(rendered_sample):
    root, settings, _ = rendered_sample
    result = run_mock_vision(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    lines = Path(result.continuity_path).read_text(encoding="utf-8").splitlines()
    candidates = [ContinuityCandidate.model_validate(json.loads(line)) for line in lines]
    assert len(candidates) == 10
    assert all(candidate.decision == "pending" for candidate in candidates)
    assert all(candidate.merge_text == "" for candidate in candidates)
    assert all(candidate.status == "candidate_only" for candidate in candidates)
    assert all(candidate.model_review_required for candidate in candidates)


def test_paths_with_spaces_and_parentheses_render(tmp_path):
    copy = tmp_path / "Sample copy (misleading 10 pages).pdf"
    shutil.copy2(SAMPLE, copy)
    settings = _settings(tmp_path, sample=copy)
    result = render_pages(copy, settings, pages="1", dpi=72, root=tmp_path)
    assert result.actual_page_count == 11
    assert result.rendered_pages == [1]


def test_full_pdf_is_blocked_before_opening_or_rendering(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("full PDF was opened")

    monkeypatch.setattr(page_module.fitz, "open", forbidden)
    with pytest.raises(PermissionError, match="full PDF"):
        render_pages(FULL, settings, pages="1", dpi=72, root=tmp_path)
    assert not Path(settings.page_image_directory).exists()


def test_phase1b_outputs_never_include_api_secret_values(tmp_path, monkeypatch):
    secret_a = "phase1b-zai-secret-value"
    secret_b = "phase1b-deepseek-secret-value"
    monkeypatch.setenv("ZAI_API_KEY", secret_a)
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_b)
    settings = _settings(tmp_path)
    render_pages(SAMPLE, settings, pages="1", dpi=72, root=tmp_path)
    run_mock_vision(SAMPLE, settings, pages="1", dpi=72, root=tmp_path)
    serialized = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in tmp_path.rglob("*.json*")
    )
    assert secret_a not in serialized
    assert secret_b not in serialized


def test_specific_manifest_detects_source_hash_change(tmp_path):
    source_copy = tmp_path / "source.pdf"
    shutil.copy2(SAMPLE, source_copy)
    settings = _settings(tmp_path, sample=source_copy)
    result = render_pages(source_copy, settings, pages="1", dpi=72, root=tmp_path)
    changed = tmp_path / "changed.pdf"
    shutil.copy2(SAMPLE, changed)
    with changed.open("ab") as handle:
        handle.write(b"changed")
    validation = validate_manifest_file(result.manifest_path, source_pdf=changed)
    assert validation.source_hash_matches is False
    assert validation.ready_for_vision is False


def test_mock_second_run_uses_cache(rendered_sample):
    root, settings, _ = rendered_sample
    run_mock_vision(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    second = run_mock_vision(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    assert second.generated_pages == []
    assert second.cached_pages == list(range(1, 12))


def test_missing_image_is_detected_and_only_that_page_is_rebuilt(tmp_path):
    result = _render(tmp_path, pages="1-2", dpi=72)
    image = Path(result.output_directory) / "page_0002.png"
    image.unlink()
    before = page_status(SAMPLE, _settings(tmp_path), pages="1-2", dpi=72, root=tmp_path)
    assert before.missing_image_pages == [2]
    resumed = _render(tmp_path, pages="1-2", dpi=72)
    assert resumed.cached_pages == [1]
    assert resumed.rendered_pages == [2]
    assert image.is_file()


def test_hash_mismatch_is_rebuilt_instead_of_reused(tmp_path):
    result = _render(tmp_path, pages="1", dpi=72)
    image = Path(result.output_directory) / "page_0001.png"
    with image.open("ab") as handle:
        handle.write(b"tamper")
    resumed = _render(tmp_path, pages="1", dpi=72)
    assert resumed.cached_pages == []
    assert resumed.rendered_pages == [1]
    assert resumed.warnings[1] == ["image_hash_mismatch"]


def test_duplicate_page_record_is_detected_by_manifest_validation(tmp_path):
    result = _render(tmp_path, pages="1", dpi=72)
    manifest_data = load_json(result.manifest_path)
    manifest_data["page_record_paths"].append(manifest_data["page_record_paths"][0])
    Path(result.manifest_path).write_text(
        json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8"
    )
    validation = validate_manifest_file(result.manifest_path)
    assert validation.duplicate_pages == [1]
    assert validation.ready_for_vision is False


def test_non_sample_requires_explicit_page_range(tmp_path):
    other = tmp_path / "Other book (test).pdf"
    shutil.copy2(SAMPLE, other)
    with pytest.raises(ValueError, match="page range is required"):
        render_pages(other, _settings(tmp_path), dpi=72, root=tmp_path)


def test_page_record_contains_traceability_and_no_guessed_printed_page(tmp_path):
    result = _render(tmp_path, pages="1", dpi=72)
    manifest = PageManifest.model_validate(load_json(result.manifest_path))
    record = PageRecord.model_validate(load_json(manifest.page_record_paths[0]))
    assert record.schema_version == "1.0"
    assert record.document_id.startswith("doc_")
    assert record.source_pdf_sha256 == sha256_file(SAMPLE)
    assert record.pdf_page == 1
    assert record.pdf_page_index == 0
    assert record.printed_page is None
    assert record.renderer == "PyMuPDF"
    assert record.image_format == "png"


def test_cached_mock_does_not_overwrite_raw_response(rendered_sample):
    root, settings, _ = rendered_sample
    first = run_mock_vision(SAMPLE, settings, pages="1", dpi=72, root=root)
    raw = Path(first.raw_directory) / "page_0001.json"
    before_mtime = raw.stat().st_mtime_ns
    run_mock_vision(SAMPLE, settings, pages="1", dpi=72, root=root)
    assert raw.stat().st_mtime_ns == before_mtime
