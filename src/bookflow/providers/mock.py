from __future__ import annotations


class MockTranslationProvider:
    calls = 0
    def health_check(self): return {"ok": True, "provider": "mock"}
    def estimate_request(self, units): return {"units": len(units), "characters": sum(len(u["source_text"]) for u in units)}
    def translate_batch(self, units):
        type(self).calls += 1
        return [{"translation_unit_id": u["translation_unit_id"], "translated_text": f"[mock] {u['source_text']}", "status": "translated"} for u in units]
    def normalize_response(self, response):
        if not isinstance(response.get("translations"), list): raise ValueError("malformed provider response")
        return response["translations"]


class MockVisionProvider:
    calls = 0
    def health_check(self): return {"ok": True, "provider": "mock"}
    def analyze_page(self, page):
        type(self).calls += 1; return {"physical_page": page["physical_page"], "observations": []}
    def normalize_response(self, response): return response
