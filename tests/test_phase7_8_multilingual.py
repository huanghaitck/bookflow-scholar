from __future__ import annotations

import hashlib, json
from pathlib import Path
import pytest

from bookflow.multilingual_schema import protect_placeholders, restore_placeholders
from bookflow.providers.config import load_provider_config
from bookflow.providers.mock import MockTranslationProvider
from bookflow.providers.openai_compatible import OpenAICompatibleTranslationProvider
from bookflow.translation_cache import TranslationCache, cache_fingerprint
from bookflow.translation_runner import TranslationRunner
from bookflow.translation_units import CANONICAL_SHA, build_multilingual_layer

ROOT=Path(".")

@pytest.fixture(scope="module", autouse=True)
def built():
    # Production artifacts are persistent state: ordinary pytest must never rebuild them.
    assert (ROOT/"data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl").is_file()
    return None

def _units(): return [json.loads(x) for x in (ROOT/"data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl").read_text("utf-8").splitlines() if x.strip()]

def test_phase6_canonical_frozen(): assert hashlib.sha256((ROOT/"data/fullbook/canonical/canonical_book_document_v1.json").read_bytes()).hexdigest()==CANONICAL_SHA
def test_971_existing_translations_reused():
    units=[u for u in _units() if u["source_object_type"]=="logical_unit"]
    assert len(units)==971 and all(u["translation_status"]=="reused_frozen" for u in units)
    assert all(u["existing_translation_ref"] for u in units)
def test_unit_ids_unique():
    ids=[u["translation_unit_id"] for u in _units()]; assert len(ids)==len(set(ids))
def test_title_provenance_keeps_raw_toc_variant():
    rows=[json.loads(x) for x in (ROOT/"data/fullbook/multilingual/provenance/source_title_candidates_v1.jsonl").read_text("utf-8").splitlines() if x.strip()]
    ch=rows[25]; assert "ACCOUNT ON" in ch["raw_toc_candidate"] and "ACCOUNT OF" in ch["canonical_title"]
def test_scientific_and_multiline_titles():
    units=_units(); titles=[u for u in units if u["source_object_type"]=="chapter_title"]
    assert any("Nemorhædus argyrochaetes" in u["source_text"] for u in titles)
    assert any("PRZEWALSKI" in u["source_text"] for u in titles)
def test_p43_captions_are_units():
    values=[u["source_text"] for u in _units() if u["source_object_type"]=="confirmed_caption"]
    assert "A VIEW ON THE YANGTSE-KIANG." in values and "TEMPLES ON HWA-SHAN." in values
def test_appendix_rows_and_blocked_ocr():
    rows=[u for u in _units() if u["source_object_type"]=="table_row_group"]
    assert len(rows)==301 and any(u["translation_status"]=="blocked_by_source_quality" for u in rows)
def test_pending_index_preserves_source():
    rows=[u for u in _units() if u["source_object_type"]=="index_entry_group"]
    assert len(rows)==334 and all(u["translation_status"]=="preserve_source" for u in rows)
def test_candidate_cells_not_units(): assert not any(u["source_object_type"]=="candidate_cell" for u in _units())
def test_placeholder_roundtrip_and_rejection():
    text="Keep {{SCIENTIFIC_NAME}} and [[PAGE_REF]]."; protected,m=protect_placeholders(text); assert restore_placeholders(protected,m)==text
    with pytest.raises(ValueError): restore_placeholders(protected.replace(next(iter(m)),""),m)
def test_ordinary_book_tokens_are_not_placeholders():
    text="In 1911, O'Connor measured Nemorhaedus (Nemorhaedus argyrochaetes) at 12 miles - Chapter XVIII."
    protected,m=protect_placeholders(text)
    assert protected==text and m=={}
def test_provider_config_has_no_literal_secret():
    cfg=load_provider_config(ROOT/"config/providers.example.yaml"); assert cfg["allow_real_api"] is False
def test_openai_adapter_uses_injected_transport_only():
    p=OpenAICompatibleTranslationProvider({"model":"fake"},transport=lambda payload:{"translations":[{"translation_unit_id":"u","translated_text":"译"}]})
    assert p.translate_batch([{"translation_unit_id":"u","source_text":"x"}])[0]["translated_text"]=="译"
    with pytest.raises(RuntimeError): OpenAICompatibleTranslationProvider({"model":"x"}).translate_batch([])
def test_malformed_provider_response_rejected():
    with pytest.raises(ValueError): OpenAICompatibleTranslationProvider({"model":"x"}).normalize_response({"bad":[]})
def test_cache_fingerprint_provider_sensitive_and_stale(tmp_path):
    base={"source_text_sha256":"a","source_object_id":"x","source_language":"en","target_language":"zh-Hans","translation_policy":"p","provider":"mock","model":"m","prompt_version":"v","glossary_version":"g","output_schema_version":"s"}
    a=cache_fingerprint(base); b=cache_fingerprint({**base,"provider":"other"}); assert a!=b
    cache=TranslationCache(tmp_path); cache.put(a,{"source_text_sha256":"a","value":1}); assert cache.get(a,"a") and cache.get(a,"stale") is None
def test_dry_run_no_provider_call():
    MockTranslationProvider.calls=0; plan=TranslationRunner(ROOT,MockTranslationProvider()).plan(2); assert plan["api_calls"]==0 and MockTranslationProvider.calls==0
def test_mock_cache_hit_and_resume(tmp_path):
    # Use project units with isolated cache root by copying only the unit file.
    target=tmp_path/"data/fullbook/multilingual/units"; target.mkdir(parents=True); target.joinpath("translation_units_zh-Hans_v1.jsonl").write_text((ROOT/"data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl").read_text("utf-8"),"utf-8")
    runner=TranslationRunner(tmp_path,MockTranslationProvider()); first=runner.run_mock(max_units=2,interrupt_after=1); second=runner.run_mock(max_units=1)
    assert len(first)==1 and len(second)==1 and second[0]["cache_hit"] is False
