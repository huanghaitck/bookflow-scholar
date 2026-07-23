"""Tests for Phase 1D full-book visual structure scan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from bookflow.structure_visual_fullbook import (
    PHASE1C_FAILURE_PAGES,
    PHASE1C_SUCCESS_PAGES,
    PHASE1D_MAX_CALLS,
    PhaseDRunResult,
    build_all_58_requests,
    build_phase1d_config,
    generate_all_58_results,
    generate_merge_preview,
    get_recall_requests,
    get_remaining_requests,
    get_remaining_pages,
    run_phase1d_scan,
    verify_frozen_files,
)
from bookflow.structure_visual_live import (
    LIVE_RUNNER_VERSION,
    LedgerEntry,
    LiveProviderConfig,
)


def _make_mock_response(page: int, role: str = "unknown", **kwargs):
    """Build a valid chat completion response dict for a page."""
    blank_kind = kwargs.pop("blank_kind", None)
    if role == "blank" and blank_kind is None:
        blank_kind = "scan_blank"
    resp = {
        "schema_version": "1.0",
        "physical_page": page,
        "primary_role": role,
        "blank_kind": blank_kind,
        "content_features": kwargs.get("content_features", []),
        "artifact_overlays": kwargs.get("artifact_overlays", []),
        "original_book_content": kwargs.get("original_book_content", False),
        "contains_prose": kwargs.get("contains_prose", False),
        "safe_to_exclude_from_prose_flow": kwargs.get("safe_to_exclude", True),
        "requires_region_analysis": kwargs.get("requires_region", False),
        "printed_page_label": kwargs.get("printed_page_label"),
        "printed_page_number": kwargs.get("printed_page_number"),
        "numbering_scheme": kwargs.get("numbering_scheme", "unknown"),
        "page_side": kwargs.get("page_side", "unknown"),
        "field_evidence": [
            {
                "field_name": "primary_role",
                "observed": "test",
                "basis": "visual",
                "confidence": 0.9,
            }
        ],
        "confidence_by_field": kwargs.get("confidence", {"primary_role": 0.9}),
        "warnings": [],
        "reviewer_notes": "",
        "raw_response_ref": None,
    }
    content_str = json.dumps(resp, ensure_ascii=False)
    return {
        "id": f"test-req-{page}",
        "model": "glm-4.6v",
        "created": 1234567890,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content_str},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _make_mock_client(page_responses: dict[int, dict]):
    """Create a mock OpenAI client factory that returns preset responses."""

    class MockClient:
        def __init__(self, **kwargs):
            self.chat = MagicMock()
            self.chat.completions = MagicMock()

            def create(**payload):
                messages = payload.get("messages", [])
                user_msg = messages[-1] if messages else {}
                content = user_msg.get("content", [])
                page = None
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            try:
                                ctx = json.loads(part["text"])
                                page = ctx.get("physical_page")
                            except (json.JSONDecodeError, KeyError):
                                pass
                if page is not None and page in page_responses:
                    return page_responses[page]
                raise RuntimeError(f"No mock response for page {page}")

            self.chat.completions.create = create

    return MockClient


# ---------------------------------------------------------------------------
# Constants and page partition tests
# ---------------------------------------------------------------------------


class TestPagePartition:
    def test_success_pages_count(self):
        assert len(PHASE1C_SUCCESS_PAGES) == 7

    def test_failure_pages_count(self):
        assert len(PHASE1C_FAILURE_PAGES) == 3

    def test_no_overlap_success_failure(self):
        assert not (PHASE1C_SUCCESS_PAGES & PHASE1C_FAILURE_PAGES)

    def test_remaining_pages_count(self):
        remaining = get_remaining_pages()
        assert len(remaining) == 48

    def test_remaining_no_overlap_with_success(self):
        remaining = set(get_remaining_pages())
        assert not (remaining & PHASE1C_SUCCESS_PAGES)

    def test_remaining_no_overlap_with_failure(self):
        remaining = set(get_remaining_pages())
        assert not (remaining & PHASE1C_FAILURE_PAGES)

    def test_all_58_partition(self):
        all_pages = PHASE1C_SUCCESS_PAGES | PHASE1C_FAILURE_PAGES | set(get_remaining_pages())
        assert len(all_pages) == 58

    def test_max_calls_is_51(self):
        assert PHASE1D_MAX_CALLS == 51

    def test_total_new_calls_3_plus_48(self):
        assert 3 + 48 == PHASE1D_MAX_CALLS


# ---------------------------------------------------------------------------
# Request building tests (use real project data)
# ---------------------------------------------------------------------------


class TestRequestBuilding:
    def test_build_all_58_requests(self, project_root):
        reqs = build_all_58_requests(project_root)
        assert len(reqs) == 58
        pages = [r.physical_page for r in reqs]
        assert len(set(pages)) == 58

    def test_recall_requests(self, project_root):
        reqs = get_recall_requests(project_root)
        assert len(reqs) == 3
        pages = {r.physical_page for r in reqs}
        assert pages == PHASE1C_FAILURE_PAGES

    def test_remaining_requests(self, project_root):
        reqs = get_remaining_requests(project_root)
        assert len(reqs) == 48

    def test_recall_and_remaining_no_overlap(self, project_root):
        recall = get_recall_requests(project_root)
        remaining = get_remaining_requests(project_root)
        recall_pages = {r.physical_page for r in recall}
        remaining_pages = {r.physical_page for r in remaining}
        assert not (recall_pages & remaining_pages)

    def test_no_success_page_in_recall_or_remaining(self, project_root):
        recall = get_recall_requests(project_root)
        remaining = get_remaining_requests(project_root)
        all_called = {r.physical_page for r in recall + remaining}
        assert not (all_called & PHASE1C_SUCCESS_PAGES)


# ---------------------------------------------------------------------------
# Dry run tests
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_no_api_calls(self, project_root):
        result = run_phase1d_scan(root=project_root, dry_run=True)
        assert result.total_api_calls == 0
        assert result.gate_messages == ["dry_run"]

    def test_dry_run_checkpoint_saved(self, project_root, tmp_path):
        """Dry run writes checkpoint to the real phase1d dir, verify it exists."""
        run_phase1d_scan(root=project_root, dry_run=True)
        cp_path = project_root / "data/fullbook/structure/phase1d/checkpoint.json"
        assert cp_path.is_file()
        cp = json.loads(cp_path.read_text("utf-8"))
        assert cp["phase"] == "phase_1d"
        assert cp["total_targets"] == 58


# ---------------------------------------------------------------------------
# Gate check tests (use temp project root to avoid touching real data)
# ---------------------------------------------------------------------------


class TestAcceptanceGate:
    @pytest.fixture(autouse=True)
    def _mock_api_key(self, monkeypatch):
        """Provide a dummy API key for mock-client tests."""
        monkeypatch.setenv("ZAI_API_KEY", "test_key_for_mock_only")

    def test_gate_passes_with_correct_responses(self, tmp_project):
        responses = {
            120: _make_mock_response(120, "blank", blank_kind="scan_blank"),
            198: _make_mock_response(198, "blank", blank_kind="scan_blank"),
            401: _make_mock_response(
                401, "appendix", requires_region=True,
                contains_prose=False, content_features=["table"],
            ),
        }
        mock_factory = _make_mock_client(responses)
        result = run_phase1d_scan(
            root=tmp_project, dry_run=False, client_factory=mock_factory,
        )
        assert result.gate_passed
        assert len(result.recall_succeeded) == 3

    def test_gate_fails_when_p120_not_blank(self, tmp_project):
        responses = {
            120: _make_mock_response(120, "chapter_body", contains_prose=True, safe_exclude=False),
            198: _make_mock_response(198, "blank", blank_kind="scan_blank"),
            401: _make_mock_response(401, "appendix", requires_region=True),
        }
        mock_factory = _make_mock_client(responses)
        result = run_phase1d_scan(
            root=tmp_project, dry_run=False, client_factory=mock_factory,
        )
        assert not result.gate_passed
        assert not result.remaining_succeeded

    def test_gate_fails_when_p401_not_appendix(self, tmp_project):
        responses = {
            120: _make_mock_response(120, "blank", blank_kind="scan_blank"),
            198: _make_mock_response(198, "blank", blank_kind="scan_blank"),
            401: _make_mock_response(401, "blank", blank_kind="scan_blank"),
        }
        mock_factory = _make_mock_client(responses)
        result = run_phase1d_scan(
            root=tmp_project, dry_run=False, client_factory=mock_factory,
        )
        assert not result.gate_passed

    def test_gate_passes_p401_with_table_no_region(self, tmp_project):
        """p401 passes gate when content_features has 'table' even if
        requires_region_analysis is false."""
        responses = {
            120: _make_mock_response(120, "blank", blank_kind="scan_blank"),
            198: _make_mock_response(198, "blank", blank_kind="scan_blank"),
            401: _make_mock_response(
                401, "appendix", requires_region=False,
                content_features=["table", "heading"],
            ),
        }
        mock_factory = _make_mock_client(responses)
        result = run_phase1d_scan(
            root=tmp_project, dry_run=False, client_factory=mock_factory,
        )
        assert result.gate_passed

    def test_gate_passes_p401_appendix_no_table_with_review(self, tmp_project):
        """p401 with appendix role but no table/region passes gate with
        a human review note instead of blocking."""
        responses = {
            120: _make_mock_response(120, "blank", blank_kind="scan_blank"),
            198: _make_mock_response(198, "blank", blank_kind="scan_blank"),
            401: _make_mock_response(
                401, "appendix", requires_region=False,
                content_features=["prose"],
            ),
        }
        mock_factory = _make_mock_client(responses)
        result = run_phase1d_scan(
            root=tmp_project, dry_run=False, client_factory=mock_factory,
        )
        assert result.gate_passed
        assert any("human review" in m for m in result.gate_messages)


# ---------------------------------------------------------------------------
# System error and content error tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.fixture(autouse=True)
    def _mock_api_key(self, monkeypatch):
        """Provide a dummy API key for mock-client tests."""
        monkeypatch.setenv("ZAI_API_KEY", "test_key_for_mock_only")

    def test_system_error_stops_batch(self, tmp_project):
        class FailingClient:
            def __init__(self, **kwargs):
                self.chat = MagicMock()
                self.chat.completions = MagicMock()
                self.chat.completions.create = MagicMock(
                    side_effect=RuntimeError("network error")
                )
        result = run_phase1d_scan(
            root=tmp_project, dry_run=False, client_factory=FailingClient,
        )
        assert result.stopped_due_to_system_error or len(result.recall_system_errors) > 0
        assert result.total_api_calls <= PHASE1D_MAX_CALLS


# ---------------------------------------------------------------------------
# Result merging tests (use real project data - read-only)
# ---------------------------------------------------------------------------


class TestResultMerging:
    def test_generate_all_58_results(self, project_root):
        path = generate_all_58_results(project_root)
        assert path.is_file()
        lines = path.read_text("utf-8").splitlines()
        records = [json.loads(l) for l in lines if l.strip()]
        assert len(records) >= 7
        assert len(records) <= 58
        pages = [r["physical_page"] for r in records]
        assert len(set(pages)) == len(records)
        assert pages == sorted(pages)

    def test_all_records_have_review_status(self, project_root):
        generate_all_58_results(project_root)
        path = project_root / "data/fullbook/structure/phase1d/all_58_visual_results.jsonl"
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                assert r["review_status"] == "pending_human_review"

    def test_source_phase_values(self, project_root):
        generate_all_58_results(project_root)
        path = project_root / "data/fullbook/structure/phase1d/all_58_visual_results.jsonl"
        phases_seen = set()
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                phases_seen.add(r["source_phase"])
        assert "phase_1c_existing_success" in phases_seen


# ---------------------------------------------------------------------------
# Merge preview tests
# ---------------------------------------------------------------------------


class TestMergePreview:
    def test_merge_preview_412_pages(self, project_root):
        generate_all_58_results(project_root)
        path = generate_merge_preview(project_root)
        assert path.is_file()
        lines = path.read_text("utf-8").splitlines()
        records = [json.loads(l) for l in lines if l.strip()]
        assert len(records) == 412
        pages = [r["physical_page"] for r in records]
        assert pages == list(range(1, 413))

    def test_non_visual_pages_keep_phase1b(self, project_root):
        generate_all_58_results(project_root)
        generate_merge_preview(project_root)
        path = project_root / "data/fullbook/structure/phase1d/fullbook_structure_merge_preview.jsonl"
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if not r["is_visual_target"]:
                    assert r["derivation_rule"] == "keep_phase1b_value"
                    assert not r["review_required"]


# ---------------------------------------------------------------------------
# Frozen files tests
# ---------------------------------------------------------------------------


class TestFrozenFiles:
    def test_frozen_files_have_sha(self, project_root):
        result = verify_frozen_files(project_root)
        assert "page_map" in result
        assert "bridge_candidates" in result
        assert "boundaries" in result
        for name, sha in result.items():
            assert sha != "FILE_NOT_FOUND", f"{name} not found"
            assert len(sha) == 64

    def test_page_map_not_modified_by_scan(self, project_root):
        before = verify_frozen_files(project_root)
        run_phase1d_scan(root=project_root, dry_run=True)
        after = verify_frozen_files(project_root)
        assert before["page_map"] == after["page_map"]
        assert before["bridge_candidates"] == after["bridge_candidates"]
        assert before["boundaries"] == after["boundaries"]


# ---------------------------------------------------------------------------
# Runner version test
# ---------------------------------------------------------------------------


class TestRunnerVersion:
    def test_runner_version_is_phase1d(self):
        assert LIVE_RUNNER_VERSION == "phase1d_live_v2"

    def test_fingerprint_includes_runner_version(self, project_root):
        from bookflow.structure_visual_live import build_live_call_fingerprint
        fp = build_live_call_fingerprint(
            offline_fingerprint="test",
            physical_page=1,
            page_image_sha256="abc",
            prompt_file_sha="def",
            system_prompt_sha="ghi",
            schema_sha="jkl",
            provider_id="zhipu",
            model="glm-4.6v",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            response_format_mode="none",
            extra_body_profile="do_sample=False,thinking=disabled",
        )
        assert len(fp) == 64
        assert fp != ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_root():
    """Return the project root path for integration tests."""
    return Path(".").resolve()


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project root with minimal structure data for
    tests that need to write Phase 1D outputs without touching real data.

    Copies only the files needed for request building and gate checking:
    - page_map.jsonl
    - ambiguous_page_targets.jsonl
    - bridge_candidates.jsonl
    - render cache images for the 3 recall pages
    - prompt file
    - config/settings.yaml
    """
    import shutil

    real_root = Path(".").resolve()
    tmp = tmp_path / "project"
    tmp.mkdir()

    # Copy required data files
    for rel in [
        "data/fullbook/structure/registry/page_map.jsonl",
        "data/fullbook/structure/phase1c/ambiguous_page_targets.jsonl",
        "data/fullbook/structure/bridges/bridge_candidates.jsonl",
        "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
    ]:
        src = real_root / rel
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            shutil.copy2(str(src), str(dst))

    # Create phase1d directory structure
    phase1d = tmp / "data/fullbook/structure/phase1d"
    phase1d.mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "normalized", "errors", "usage", "request_metadata", "requests"):
        (phase1d / sub).mkdir(parents=True, exist_ok=True)

    # Copy page images for recall pages (120, 198, 401) from page_map
    # source_page_asset_ref paths, not from cache/render
    import json as _json
    page_map_path = tmp / "data/fullbook/structure/registry/page_map.jsonl"
    if page_map_path.is_file():
        for line in page_map_path.read_text("utf-8").splitlines():
            if line.strip():
                d = _json.loads(line)
                if d["physical_page"] in (120, 198, 401):
                    ref = d.get("source_page_asset_ref", "")
                    if ref:
                        src = real_root / ref
                        dst = tmp / ref
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        if src.is_file():
                            shutil.copy2(str(src), str(dst))

    # Copy prompt file
    prompt_src = real_root / "prompts/structure_page_classification_v1.md"
    prompt_dst = tmp / "prompts/structure_page_classification_v1.md"
    prompt_dst.parent.mkdir(parents=True, exist_ok=True)
    if prompt_src.is_file():
        shutil.copy2(str(prompt_src), str(prompt_dst))

    # Copy config/settings
    config_src = real_root / "config/settings.yaml"
    config_dst = tmp / "config/settings.yaml"
    config_dst.parent.mkdir(parents=True, exist_ok=True)
    if config_src.is_file():
        shutil.copy2(str(config_src), str(config_dst))

    return tmp
