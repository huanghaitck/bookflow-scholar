"""Tests for Phase 1C-B offline request builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bookflow.structure_visual_requests import (
    build_sample_batch_requests,
    build_visual_page_request,
    validate_request_asset_refs,
    write_sample_batch_requests,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = ROOT / "data/fullbook/structure/phase1c/sample_batch_v1.json"
TARGETS_PATH = ROOT / "data/fullbook/structure/phase1c/ambiguous_page_targets.jsonl"

pytestmark = pytest.mark.skipif(
    not SAMPLE_PATH.is_file() or not TARGETS_PATH.is_file(),
    reason="Phase 1C-A output files not found",
)


def _load_sample_pages() -> list[int]:
    return json.loads(SAMPLE_PATH.read_text("utf-8"))["selected_pages"]


class TestRequestBuilder:
    def test_all_10_pages_generate_requests(self):
        requests = build_sample_batch_requests(ROOT)
        assert len(requests) == 10

    def test_page_numbers_match_sample_batch(self):
        requests = build_sample_batch_requests(ROOT)
        req_pages = sorted(r.physical_page for r in requests)
        sample_pages = sorted(_load_sample_pages())
        assert req_pages == sample_pages

    def test_asset_refs_all_relative(self):
        requests = build_sample_batch_requests(ROOT)
        violations = validate_request_asset_refs(requests)
        assert violations == []

    def test_no_base64_in_requests(self):
        requests = build_sample_batch_requests(ROOT)
        for req in requests:
            dumped = req.model_dump_json()
            assert "base64" not in dumped.lower()

    def test_no_api_key_in_requests(self):
        requests = build_sample_batch_requests(ROOT)
        for req in requests:
            dumped = req.model_dump_json().lower()
            assert "api_key" not in dumped
            assert "apikey" not in dumped
            assert "authorization" not in dumped

    def test_no_full_ocr_text(self):
        """Requests must not contain large blocks of OCR text."""
        requests = build_sample_batch_requests(ROOT)
        for req in requests:
            # Evidence summary should be short strings, not full paragraphs
            for line in req.context.current_evidence_summary:
                assert len(line) < 500, (
                    f"p{req.physical_page}: evidence line too long ({len(line)} chars)"
                )
            # Neighboring roles are short strings
            for role in req.context.neighboring_primary_roles:
                assert len(role) < 50

    def test_fingerprint_deterministic(self):
        """Same input must produce same fingerprint."""
        requests1 = build_sample_batch_requests(ROOT)
        requests2 = build_sample_batch_requests(ROOT)
        for r1, r2 in zip(requests1, requests2):
            assert r1.request_fingerprint == r2.request_fingerprint

    def test_fingerprint_differs_across_pages(self):
        """Different pages must have different fingerprints."""
        requests = build_sample_batch_requests(ROOT)
        fingerprints = [r.request_fingerprint for r in requests]
        assert len(fingerprints) == len(set(fingerprints))

    def test_request_id_format(self):
        requests = build_sample_batch_requests(ROOT)
        for req in requests:
            assert req.request_id == f"visreq_p{req.physical_page:04d}"


class TestWriteRequests:
    def test_write_jsonl_and_verify(self):
        output_path = ROOT / "data/fullbook/structure/phase1c/sample_batch_v1_requests.jsonl"
        result_path = write_sample_batch_requests(ROOT)
        assert result_path == output_path

        lines = [l for l in output_path.read_text("utf-8").splitlines() if l.strip()]
        assert len(lines) == 10

        for line in lines:
            d = json.loads(line)
            assert "physical_page" in d
            assert "context" in d
            assert "request_fingerprint" in d
            # No absolute paths
            ref = d["context"]["source_page_asset_ref"]
            assert not (len(ref) >= 2 and ref[1] == ":")
            assert not ref.startswith("/")
            assert not ref.startswith("\\")

    def test_idempotent_output_hash(self):
        """Two writes must produce identical file hashes."""
        path1 = write_sample_batch_requests(ROOT)
        hash1 = hashlib.sha256(path1.read_bytes()).hexdigest()
        path2 = write_sample_batch_requests(ROOT)
        hash2 = hashlib.sha256(path2.read_bytes()).hexdigest()
        assert hash1 == hash2

    def test_pages_match_sample_batch(self):
        write_sample_batch_requests(ROOT)
        path = ROOT / "data/fullbook/structure/phase1c/sample_batch_v1_requests.jsonl"
        lines = [l for l in path.read_text("utf-8").splitlines() if l.strip()]
        req_pages = sorted(json.loads(l)["physical_page"] for l in lines)
        sample_pages = sorted(_load_sample_pages())
        assert req_pages == sample_pages
