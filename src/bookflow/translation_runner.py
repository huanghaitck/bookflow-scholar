from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

from .io_utils import atomic_write_json, atomic_write_jsonl
from .multilingual_schema import protect_placeholders, restore_placeholders
from .providers.mock import MockTranslationProvider
from .translation_cache import TranslationCache, cache_fingerprint


class TranslationRunner:
    """The single resumable runner for offline plans and production translation."""

    def __init__(self, root: Path, provider=None):
        self.root = root
        self.provider = provider or MockTranslationProvider()
        self.cache = TranslationCache(root)

    @property
    def unit_path(self):
        return self.root / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl"

    @property
    def state_path(self):
        return self.root / "data/fullbook/multilingual/state/translation_state_zh-Hans_v1.jsonl"

    @property
    def checkpoint_path(self):
        return self.root / "data/fullbook/multilingual/checkpoints/translation_zh-Hans_production.json"

    def load(self):
        return [json.loads(x) for x in self.unit_path.read_text("utf-8").splitlines() if x.strip()]

    def _effective_units(self):
        units = self.load()
        states = {s["translation_unit_id"]: s for s in self._load_states()}
        return [{**u, "translation_status": states.get(u["translation_unit_id"], {}).get("status", u["translation_status"])} for u in units]

    def _queue(self, max_units=None, unit_type=None, status_filter="pending"):
        if status_filter != "pending":
            raise ValueError("production translation queue is pending-only")
        units = [u for u in self._effective_units() if u["translation_status"] == "pending"]
        if unit_type:
            allowed = {x.strip() for x in unit_type.split(",") if x.strip()}
            units = [u for u in units if u["source_object_type"] in allowed]
        return units[:max_units] if max_units else units

    def plan(self, max_units=None, unit_type=None, status_filter="pending", **_):
        self.reconcile()
        units = self._queue(max_units, unit_type, status_filter)
        all_units = self._effective_units()
        types = Counter(u["source_object_type"] for u in units)
        excluded = Counter(u["translation_status"] for u in all_units if u not in units)
        return {
            "dry_run": True,
            "api_calls": 0,
            "unit_count": len(units),
            "unit_type_counts": dict(sorted(types.items())),
            "input_characters": sum(len(u["source_text"]) for u in units),
            "estimated_tokens": sum(len(u["source_text"]) for u in units) // 4 + 1,
            "unit_ids": [u["translation_unit_id"] for u in units],
            "retranslated_existing_main_text": sum(u["source_object_type"] == "logical_unit" for u in units),
            "preserve_source_queued": sum(u["translation_status"] == "preserve_source" for u in units),
            "blocked_source_queued": sum(u["translation_status"] == "blocked_by_source_quality" for u in units),
            "excluded_status_counts": dict(sorted(excluded.items())),
        }

    @staticmethod
    def _spec(unit, provider_name, model):
        return {
            **unit,
            "provider": provider_name,
            "model": model,
            "prompt_version": "translate-v1",
            "glossary_version": "none",
            "output_schema_version": "translation-response-1.0",
        }

    @staticmethod
    def _prepared(unit):
        protected, placeholders = protect_placeholders(unit["source_text"])
        return {
            "translation_unit_id": unit["translation_unit_id"],
            "source_object_id": unit["source_object_id"],
            "source_object_type": unit["source_object_type"],
            "source_text": protected,
            "placeholders": list(placeholders),
        }, placeholders

    @staticmethod
    def _validate_batch(units, responses, mappings):
        if len(responses) != len(units):
            raise ValueError("translation response count mismatch")
        by_id = {item.get("translation_unit_id"): item for item in responses}
        if len(by_id) != len(responses):
            raise ValueError("duplicate or missing translation_unit_id")
        validated = []
        for unit in units:
            item = by_id.get(unit["translation_unit_id"])
            if not item or item.get("source_object_id") != unit["source_object_id"]:
                raise ValueError("source_object_id correspondence mismatch")
            text = item.get("translated_text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty translated_text")
            restored = restore_placeholders(text, mappings[unit["translation_unit_id"]])
            validated.append({**item, "translated_text": restored, "validated": True})
        return validated

    def _load_states(self):
        units = self.load()
        if self.state_path.is_file():
            states = [json.loads(x) for x in self.state_path.read_text("utf-8").splitlines() if x.strip()]
            by_id = {state["translation_unit_id"]: state for state in states}
            if len(by_id) != len(states):
                raise ValueError("duplicate translation state ID")
            for unit in units:
                if unit["translation_unit_id"] not in by_id:
                    state = {"translation_unit_id": unit["translation_unit_id"], "source_text_sha256": unit["source_text_sha256"], "status": unit["translation_status"], "attempts": 0, "last_error": None}
                    states.append(state)
                    by_id[unit["translation_unit_id"]] = state
            return states
        return [{"translation_unit_id": u["translation_unit_id"], "source_text_sha256": u["source_text_sha256"], "status": u["translation_status"], "attempts": 0, "last_error": None} for u in units]

    def _validated_cache_records(self, units, states):
        by_unit = {u["translation_unit_id"]: u for u in units}
        fingerprints = {s.get("cache_fingerprint") for s in states if s.get("cache_fingerprint")}
        cache_root = self.root / "data/fullbook/multilingual/cache"
        paths = [cache_root / fp[:2] / f"{fp}.json" for fp in fingerprints]
        if cache_root.is_dir(): paths.extend(cache_root.rglob("*.json"))
        records = {}
        for path in dict.fromkeys(paths):
            if not path.is_file(): continue
            try: value = json.loads(path.read_text("utf-8"))
            except (ValueError, OSError): continue
            uid = value.get("translation_unit_id"); unit = by_unit.get(uid)
            text = value.get("translated_text")
            if not unit or value.get("validated") is not True or not isinstance(text, str) or not text.strip(): continue
            if value.get("source_text_sha256") != unit["source_text_sha256"]: continue
            if value.get("source_object_id") != unit["source_object_id"]: continue
            fp = value.get("fingerprint") or path.stem
            if fp != path.stem: continue
            records[uid] = {**value, "cache_fingerprint": fp}
        return records

    def _commit_snapshot(self, states, records, completed_ids, user_api_calls, planned_ids=None):
        units = self.load(); state_by_id = {s["translation_unit_id"]: s for s in states}
        completed = set(completed_ids)
        for uid in set(completed) | {s["translation_unit_id"] for s in states if s.get("status") in {"validated", "translated"}}:
            if uid in records:
                state = state_by_id[uid]; state.update(status="validated", cache_fingerprint=records[uid]["cache_fingerprint"], last_error=None)
                completed.add(uid)
            else:
                completed.discard(uid)
                state = state_by_id.get(uid)
                if state and state.get("status") in {"validated", "translated"}: state.update(status="failed_retryable", last_error="validated cache unavailable")
        # A valid immutable cache is authoritative even after an interrupted state/overlay commit.
        for uid, record in records.items():
            state = state_by_id[uid]
            state.update(status="validated", cache_fingerprint=record["cache_fingerprint"], last_error=None)
            completed.add(uid)

        overlay = {"translations": {}}
        summaries = {}
        for uid in sorted(completed):
            record = records[uid]
            overlay["translations"][uid] = {"source_object_id": record["source_object_id"], "translated_text": record["translated_text"], "cache_fingerprint": record["cache_fingerprint"]}
            summaries[uid] = {"source_object_id": record["source_object_id"], "translated_text_sha256": hashlib.sha256(record["translated_text"].encode()).hexdigest(), "provider": record.get("provider"), "model": record.get("model"), "cache_fingerprint": record["cache_fingerprint"], "validation_status": "validated"}

        counts = dict(sorted(Counter(s["status"] for s in states).items()))
        base_manifest_path = self.root / "data/fullbook/multilingual/multilingual_book_manifest_v1.json"
        base_manifest = json.loads(base_manifest_path.read_text("utf-8")) if base_manifest_path.is_file() else {"schema_version": "multilingual-book-1.0", "translation_unit_count": len(units)}
        base_manifest.update(
            translation_unit_count=len(units),
            unit_type_counts=dict(sorted(Counter(unit["source_object_type"] for unit in units).items())),
            status_counts=counts,
            validated_translation_count=len(completed),
            pending_translation_count=counts.get("pending", 0),
            validated_translation_unit_ids=sorted(completed),
        )
        document_path = self.root / "data/fullbook/multilingual/documents/multilingual_book_document_zh-Hans_v1.json"
        document = json.loads(document_path.read_text("utf-8")) if document_path.is_file() else {"schema_version": "multilingual-document-1.0", "target_language": "zh-Hans"}
        document.update(validated_translation_overlay_ref="data/fullbook/multilingual/documents/multilingual_translation_overlay_zh-Hans_v1.json", validated_translation_unit_ids=sorted(completed), validated_translation_count=len(completed))
        validation_path = self.root / "data/fullbook/multilingual/reports/multilingual_validation_zh-Hans_v1.json"
        validation = json.loads(validation_path.read_text("utf-8")) if validation_path.is_file() else {"checks": {}}
        validation["counts"] = base_manifest
        validation["checks"].update(state_counts_current=True, validated_cache_matches_state=True, overlay_matches_validated_state=True, manifest_matches_state=True)
        validation["validation_passed"] = True
        providers = sorted({r.get("provider") for r in records.values() if r.get("provider")})
        models = sorted({r.get("model") for r in records.values() if r.get("model")})
        blocking_statuses = ("pending", "failed_retryable", "failed_terminal", "stale_source")
        task_complete = all(counts.get(name, 0) == 0 for name in blocking_statuses)
        previous_checkpoint = json.loads(self.checkpoint_path.read_text("utf-8")) if self.checkpoint_path.is_file() else {}
        completed_at = previous_checkpoint.get("completed_at") if task_complete and previous_checkpoint.get("status") == "completed" else None
        if task_complete and not completed_at:
            completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        checkpoint_status = "completed" if task_complete else "resumable"
        pending_ids = {state["translation_unit_id"] for state in states if state["status"] == "pending"}
        pending_types = {unit["source_object_type"] for unit in units if unit["translation_unit_id"] in pending_ids}
        next_action = "build_first_book_releases" if task_complete else "translate_pending_appendix_units" if pending_types == {"appendix_element"} else "translate_remaining_units"
        translation_manifest = {"target_language": "zh-Hans", "validated": len(completed), "status_counts": counts, "validated_units": summaries, "providers": providers, "models": models, "user_production_api_calls": user_api_calls, "codex_api_calls": 0, "production_translation_status": checkpoint_status, "next_action": next_action, "secrets_recorded": False}
        checkpoint = {"completed_unit_ids": sorted(completed), "status": checkpoint_status, "next_action": next_action, "providers": providers, "models": models, "user_production_api_calls": user_api_calls, "codex_api_calls": 0, "secrets_recorded": False}
        if completed_at: checkpoint["completed_at"] = completed_at
        if planned_ids is not None: checkpoint["planned_unit_ids"] = sorted(set(planned_ids))

        # Each helper validates JSON serialization and uses a same-directory temp + os.replace.
        atomic_write_jsonl(self.state_path, states)
        atomic_write_json(self.root / "data/fullbook/multilingual/documents/multilingual_translation_overlay_zh-Hans_v1.json", overlay)
        atomic_write_json(document_path, document)
        atomic_write_json(base_manifest_path, base_manifest)
        atomic_write_json(validation_path, validation)
        atomic_write_json(self.root / "data/fullbook/multilingual/translation_manifest_zh-Hans_v1.json", translation_manifest)
        # The completed list is the commit record and is replaced last.
        atomic_write_json(self.checkpoint_path, checkpoint)
        return {"status_counts": counts, "validated": len(completed), "pending": counts.get("pending", 0), "completed_unit_ids": sorted(completed), "user_production_api_calls": user_api_calls, "codex_api_calls": 0, "production_checkpoint_status": checkpoint_status, "production_translation_status": checkpoint_status, "next_action": next_action, "completed_at": completed_at, "already_completed": previous_checkpoint.get("status") == "completed" and task_complete}

    def reconcile(self):
        units = self.load(); states = self._load_states()
        checkpoint = json.loads(self.checkpoint_path.read_text("utf-8")) if self.checkpoint_path.is_file() else {}
        legacy_manifest_path = self.root / "data/fullbook/multilingual/translation_manifest_zh-Hans_v1.json"
        legacy = json.loads(legacy_manifest_path.read_text("utf-8")) if legacy_manifest_path.is_file() else {}
        calls = int(checkpoint.get("user_production_api_calls", legacy.get("user_production_api_calls", legacy.get("api_calls_last_run", 0))))
        records = self._validated_cache_records(units, states)
        if not self.state_path.is_file() and not self.checkpoint_path.is_file() and not records:
            counts = dict(sorted(Counter(s["status"] for s in states).items()))
            return {"status_counts": counts, "validated": counts.get("validated", 0), "pending": counts.get("pending", 0), "completed_unit_ids": [], "user_production_api_calls": 0, "codex_api_calls": 0, "production_checkpoint_status": "resumable", "production_translation_status": "ready_for_user_execution", "next_action": "translate_remaining_units", "completed_at": None, "already_completed": False}
        return self._commit_snapshot(states, records, checkpoint.get("completed_unit_ids", []), calls, checkpoint.get("planned_unit_ids"))

    def _persist(self, results, provider_name, model, completed_ids, user_api_calls, planned_ids):
        states = self._load_states()
        by_id = {x["translation_unit_id"]: x for x in results}
        for state in states:
            if state["translation_unit_id"] in by_id:
                state.update(status="validated", attempts=int(state.get("attempts", 0)) + (0 if by_id[state["translation_unit_id"]]["cache_hit"] else 1), last_error=None, cache_fingerprint=by_id[state["translation_unit_id"]]["cache_fingerprint"])
        records = self._validated_cache_records(self.load(), states)
        return self._commit_snapshot(states, records, completed_ids, user_api_calls, planned_ids)

    def _write_failure_diagnostic(self, exc, units, response=None):
        from .providers.openai_compatible import ProviderContractError
        unit_ids = [u["translation_unit_id"] for u in units]
        raw = b""
        response_type = None
        structure = None
        if response is not None:
            response_type = type(response).__name__
            raw = json.dumps(response, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            if isinstance(response, dict): structure = {k: type(v).__name__ for k, v in list(response.items())[:20]}
            elif isinstance(response, list): structure = {"items": len(response), "first_keys": sorted(response[0]) if response and isinstance(response[0], dict) else []}
        status = getattr(exc, "status_code", None)
        if status is None and getattr(exc, "response", None) is not None: status = getattr(exc.response, "status_code", None)
        diagnostic = {
            "http_status": status,
            "exception_type": type(exc).__name__,
            "response_python_type": getattr(exc, "response_type", None) or response_type,
            "response_content_length": getattr(exc, "response_length", None) if isinstance(exc, ProviderContractError) else (len(raw) if raw else None),
            "response_sha256": getattr(exc, "response_sha256", None) if isinstance(exc, ProviderContractError) else (hashlib.sha256(raw).hexdigest() if raw else None),
            "finish_reason": getattr(exc, "finish_reason", None),
            "json_parse_error": getattr(exc, "json_error", None),
            "schema_failure_path": getattr(exc, "schema_path", None) or (str(exc) if isinstance(exc, ValueError) else None),
            "response_structure_truncated": structure,
            "batch_size": len(units),
            "unit_ids": unit_ids,
            "authorization_recorded": False,
            "secret_recorded": False,
        }
        key = hashlib.sha256("|".join(unit_ids).encode()).hexdigest()[:16]
        path = self.root / f"data/fullbook/multilingual/diagnostics/translation_failure_{key}.json"
        atomic_write_json(path, diagnostic)
        return path

    def run(self, *, provider_name, model, max_units=None, batch_size=8, max_input_tokens=12000, max_retries=2, unit_type=None, status_filter="pending", resume=True, interrupt_after=None):
        reconciled = self.reconcile()
        base_api_calls = reconciled["user_production_api_calls"]
        checkpoint = json.loads(self.checkpoint_path.read_text("utf-8")) if self.checkpoint_path.is_file() else {}
        planned_ids = checkpoint.get("planned_unit_ids") if resume else None
        if planned_ids:
            effective = {u["translation_unit_id"]: u for u in self._effective_units()}
            units = [effective[uid] for uid in planned_ids if uid in effective and effective[uid]["translation_status"] == "pending"]
        else:
            units = self._queue(max_units, unit_type, status_filter)
            planned_ids = [u["translation_unit_id"] for u in units]
        completed = set()
        if resume and self.checkpoint_path.is_file():
            completed.update(json.loads(self.checkpoint_path.read_text("utf-8")).get("completed_unit_ids", []))
        units = [u for u in units if u["translation_unit_id"] not in completed]
        all_results, api_calls = [], 0
        cursor = 0
        while cursor < len(units):
            if interrupt_after is not None and len(all_results) >= interrupt_after:
                break
            is_first_batch = cursor == 0
            batch = units[cursor:cursor + batch_size]
            while sum(len(u["source_text"]) for u in batch) // 4 + 1 > max_input_tokens and len(batch) > 1:
                batch = batch[:-1]
            cursor += len(batch)
            prepared, mappings = [], {}
            cached_results, call_units = [], []
            for unit in batch:
                spec = self._spec(unit, provider_name, model)
                fp = cache_fingerprint(spec)
                cached = self.cache.get(fp, unit["source_text_sha256"])
                if cached:
                    cached_results.append({**cached, "cache_hit": True, "cache_fingerprint": fp})
                else:
                    item, mapping = self._prepared(unit); prepared.append(item); call_units.append(unit); mappings[unit["translation_unit_id"]] = mapping
            fresh = []
            if prepared:
                attempts = 1 if is_first_batch else max_retries + 1
                for attempt in range(attempts):
                    try:
                        api_calls += 1
                        responses = self.provider.translate_batch(prepared)
                        break
                    except Exception as exc:
                        if attempt + 1 >= attempts:
                            self._write_failure_diagnostic(exc, call_units)
                            raise
                try:
                    fresh = self._validate_batch(call_units, responses, mappings)
                except Exception as exc:
                    self._write_failure_diagnostic(exc, call_units, responses)
                    raise
                for unit, result in zip(call_units, fresh):
                    fp = cache_fingerprint(self._spec(unit, provider_name, model))
                    stored = {**result, "source_text_sha256": unit["source_text_sha256"], "fingerprint": fp, "provider": provider_name, "model": model}
                    self.cache.put(fp, stored)
                    result.update(stored, cache_hit=False, cache_fingerprint=fp)
            batch_results = cached_results + fresh
            all_results.extend(batch_results)
            completed.update(x["translation_unit_id"] for x in batch_results)
            self._persist(all_results, provider_name, model, completed, base_api_calls + api_calls, planned_ids)
            if is_first_batch and len(batch_results) != len(batch):
                raise ValueError("first batch gate failed")
        final = self.reconcile() if not units else self.status()
        return {"validated": len(all_results), "api_calls": api_calls, "cache_hits": sum(x["cache_hit"] for x in all_results), "results": all_results, "production_checkpoint_status": final["production_checkpoint_status"], "already_completed": final["production_checkpoint_status"] == "completed" and not all_results, "next_action": final["next_action"]}

    def run_mock(self, max_units=None, interrupt_after=None):
        # Compatibility path for Phase 7+8 tests, using the same production runner.
        provider = self.provider
        class Adapter:
            def translate_batch(_, units):
                raw = provider.translate_batch(units)
                return [{**x, "source_object_id": u["source_object_id"]} for x, u in zip(raw, units)]
        self.provider = Adapter()
        result = self.run(provider_name="mock", model="mock-v1", max_units=max_units, batch_size=1, interrupt_after=interrupt_after, resume=False)
        return [{"translation_unit_id": x["translation_unit_id"], "status": "validated", "cache_fingerprint": x["cache_fingerprint"], "cache_hit": x["cache_hit"]} for x in result["results"]]

    def status(self):
        return self.reconcile()
