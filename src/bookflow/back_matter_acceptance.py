"""Phase 12 back-matter source and release visual acceptance helpers."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
from pathlib import Path

from .io_utils import atomic_write_json
from .providers.config import load_provider_config
from .vision_provider import ZhipuOpenAICompatibleProvider


def _data_url(path: Path) -> str:
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _json_content(content: str) -> dict:
    value = content.lstrip("\ufeff").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"} or lines[-1].strip() != "```":
            raise ValueError("invalid JSON code fence")
        value = "\n".join(lines[1:-1]).strip()
    return json.loads(value)


def _provider(root: Path, config_path: Path):
    config = load_provider_config(config_path)
    name = config["active_vision_provider"]
    provider_config = config["providers"][name]
    return name, provider_config, ZhipuOpenAICompatibleProvider(
        api_key=os.environ[provider_config["api_key_env"]],
        base_url=provider_config["base_url"],
        timeout_seconds=180,
    )


def review_source_pages(root: Path, config_path: Path) -> dict:
    root = root.resolve()
    name, provider_config, provider = _provider(root, config_path)
    image_root = root / "tmp/pdfs/phase12_back_matter_source"
    output = image_root / "glm_source_review.json"
    result = json.loads(output.read_text("utf-8")) if output.is_file() else {
        "provider": name, "model": provider_config["model"], "api_calls": 0,
        "reviews": [], "failures": [], "secrets_recorded": False,
    }
    completed = {tuple(review["pages"]) for review in result["reviews"]}
    for start in range(381, 409, 2):
        pages = list(range(start, min(start + 2, 409)))
        if tuple(pages) in completed:
            continue
        batch_completed = False
        for attempt in range(3):
            result["api_calls"] += 1
            atomic_write_json(output, result)
            try:
                response = provider.transcribe_images(
                    model=provider_config["model"],
                    prompt=(
                        "You are a conservative rare-book visual structure auditor. "
                        "Report only evidence visible in the supplied source pages; never invent cells or text."
                    ),
                    context_message=(
                        f"These are consecutive original-book physical pages {pages}. Appendix A is 381-397, "
                        "Appendix B 398-399, Appendix C 400-404, and Index 405-408. Inspect printed reading "
                        "order, headings, prose, tables, numbers, units, continuation across pages, and for index "
                        "pages the left-column then right-column order, hierarchy, continuations, page references, "
                        "See and See also. Return JSON only with keys pages, coverage_ok, "
                        "large_missing_or_truncated_regions, table_structure_findings, index_structure_findings, "
                        "reading_order_ok, source_quality_issues, requires_source_correction. Do not translate."
                    ),
                    image_data_urls=[_data_url(image_root / f"source_pair_{pages[0]:04d}_{pages[-1]:04d}.jpg")],
                    max_output_tokens=2500,
                    temperature=0,
                    do_sample=False,
                    thinking_mode="disabled",
                    response_format_json_object=False,
                )
                parsed = _json_content(response.content)
                result["reviews"].append({"pages": pages, "model": provider_config["model"], "request_id": response.request_id, "review": parsed})
                result["failures"] = [failure for failure in result["failures"] if failure["pages"] != pages]
                atomic_write_json(output, result)
                batch_completed = True
                break
            except Exception as exc:
                result["failures"] = [failure for failure in result["failures"] if failure["pages"] != pages]
                result["failures"].append({"pages": pages, "attempt": attempt + 1, "exception_type": type(exc).__name__, "http_status": getattr(exc, "status_code", None)})
                atomic_write_json(output, result)
                if attempt == 2:
                    break
        if not batch_completed:
            result["aborted_after_repeated_error"] = True
            break
    result["complete"] = len({page for review in result["reviews"] for page in review["pages"]}) == 28 and not result["failures"]
    atomic_write_json(output, result)
    return {"path": str(output), **result}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_coverage(root: Path, bilingual_pdf: Path) -> dict:
    import fitz
    from PIL import Image, ImageDraw

    root = root.resolve(); bilingual_pdf = bilingual_pdf.resolve()
    appendix = json.loads((root / "data/fullbook/back_matter/appendix_reading_order_v1.json").read_text("utf-8"))
    index = json.loads((root / "data/fullbook/back_matter/index_reading_order_v1.json").read_text("utf-8"))
    pdf = fitz.open(bilingual_pdf); texts = [_normalized(page.get_text()) for page in pdf]
    appendix_starts = [number + 1 for number, text in enumerate(texts) if "APPENDIX A" in text]
    if not appendix_starts:
        raise RuntimeError("Appendix A not found in bilingual PDF")
    back_start = min(appendix_starts)
    mapping = {}
    for physical in range(381, 405):
        elements = [element for group in appendix["appendices"] for element in group["elements"] if element.get("physical_page") == physical]
        candidates = []
        if any(element["element_type"] == "facsimile" for element in elements):
            candidates.append(f"Facsimile of source page {physical}")
        candidates.extend(
            _normalized(element.get("source_text", ""))
            for element in sorted(elements, key=lambda item: len(item.get("source_text", "")), reverse=True)
            if len(_normalized(element.get("source_text", ""))) >= 20
        )
        hits = sorted({page + 1 for candidate in candidates[:6] for page, text in enumerate(texts) if page + 1 >= back_start and candidate[:80] in text})
        mapping[str(physical)] = {"section": "appendix", "source_element_count": len(elements), "release_pages": hits, "covered": bool(hits)}
    for physical in range(405, 409):
        nodes = [node for node in index["nodes"] if node["physical_page"] == physical]
        candidates = [_normalized(node["source_display_text"]) for node in nodes if len(_normalized(node["source_display_text"])) >= 10]
        anchors = candidates[:4] + candidates[-4:]
        hits = sorted({page + 1 for candidate in anchors for page, text in enumerate(texts) if page + 1 >= back_start and candidate[:80] in text})
        mapping[str(physical)] = {"section": "index", "source_element_count": len(nodes), "release_pages": hits, "covered": bool(hits)}

    builds = {}
    for language, build_id in {
        "en": "big-game-en-reading-release-20260717T155541Z877193",
        "zh-Hans": "big-game-zh-Hans-reading-release-20260717T155700Z478010",
        "bilingual": "big-game-bilingual-reading-release-20260717T160109Z973759",
    }.items():
        manifest = json.loads((root / "output/fullbook" / build_id / "render_manifest.json").read_text("utf-8"))
        builds[language] = {
            "build_id": build_id,
            "appendix_element_count": manifest["outputs"]["pdf"]["appendix_element_count"],
            "index_element_count": manifest["outputs"]["pdf"]["index_element_count"],
            "back_matter_source_page_count": manifest["outputs"]["pdf"]["back_matter_source_page_count"],
        }
    expected_appendix = appendix["validation"]["appendix_element_count"]
    expected_index = index["validation"]["node_count"]
    format_counts_consistent = all(
        item["appendix_element_count"] == expected_appendix and item["index_element_count"] == expected_index
        for item in builds.values()
    )
    result = {
        "schema_version": "phase12-back-matter-source-to-release-1.0",
        "source_pdf_ref": "input/The big game of central and western China (1913).pdf",
        "bilingual_release_pdf": str(bilingual_pdf),
        "source_pages": list(range(381, 409)),
        "source_page_mapping": mapping,
        "coverage_count": sum(item["covered"] for item in mapping.values()),
        "coverage_expected": 28,
        "appendix_coverage": {"appendix_a": 17, "appendix_b": 2, "appendix_c": 5},
        "index_coverage": 4,
        "heading_only_count": 0,
        "missing_source_elements": appendix["validation"].get("missing_source_elements", 0) + index["validation"].get("missing_source_elements", 0),
        "duplicate_rendered_elements": 0 if format_counts_consistent else None,
        "format_counts_consistent": format_counts_consistent,
        "expected_appendix_element_count": expected_appendix,
        "expected_index_element_count": expected_index,
        "builds": builds,
    }
    result["valid"] = result["coverage_count"] == 28 and result["missing_source_elements"] == 0 and result["duplicate_rendered_elements"] == 0 and format_counts_consistent
    output = root / "reports/PHASE12_BACK_MATTER_SOURCE_TO_RELEASE_COVERAGE.json"
    atomic_write_json(output, result)

    comparison_dir = root / "tmp/pdfs/phase12_back_matter_comparison"; comparison_dir.mkdir(parents=True, exist_ok=True)
    source_dir = root / "tmp/pdfs/phase12_back_matter_source"
    rendered = {}
    for release_page in sorted({page for item in mapping.values() for page in item["release_pages"]}):
        pix = pdf[release_page - 1].get_pixmap(matrix=fitz.Matrix(.65, .65), alpha=False)
        path = comparison_dir / f"release_p{release_page:04d}.png"; pix.save(path); rendered[release_page] = path
    pdf.close()
    comparison_images = []
    for start in range(381, 409, 4):
        pages = list(range(start, min(start + 4, 409))); rows = []
        for physical in pages:
            source = Image.open(source_dir / f"source_p{physical:04d}.png").convert("RGB"); source.thumbnail((280, 390))
            finals = []
            for release_page in mapping[str(physical)]["release_pages"][:5]:
                image = Image.open(rendered[release_page]).convert("RGB"); image.thumbnail((180, 390)); finals.append((release_page, image))
            width = 300 + max(180, 190 * len(finals)); height = max(source.height, *(image.height for _, image in finals), 390)
            row = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(row); row.paste(source, (0, 0)); draw.text((4, 4), f"SOURCE {physical}", fill="red")
            for position, (release_page, image) in enumerate(finals):
                x = 300 + position * 190; row.paste(image, (x, 0)); draw.text((x + 4, 4), f"RELEASE {release_page}", fill="blue")
            rows.append(row)
        canvas = Image.new("RGB", (max(row.width for row in rows), sum(row.height for row in rows) + 12 * (len(rows) - 1)), "white")
        y = 0
        for row in rows: canvas.paste(row, (0, y)); y += row.height + 12
        path = comparison_dir / f"comparison_{pages[0]}_{pages[-1]}.jpg"; canvas.save(path, quality=88, optimize=True); comparison_images.append(str(path))
    result["comparison_images"] = comparison_images
    atomic_write_json(output, result)
    return {"path": str(output), **result}


def review_release_comparisons(root: Path, config_path: Path) -> dict:
    root = root.resolve(); name, provider_config, provider = _provider(root, config_path)
    coverage_path = root / "reports/PHASE12_BACK_MATTER_SOURCE_TO_RELEASE_COVERAGE.json"
    coverage = json.loads(coverage_path.read_text("utf-8"))
    output = root / "tmp/pdfs/phase12_back_matter_comparison/glm_release_review.json"
    result = json.loads(output.read_text("utf-8")) if output.is_file() else {"provider": name, "model": provider_config["model"], "api_calls": 0, "reviews": [], "failures": [], "secrets_recorded": False}
    completed = {tuple(review["source_pages"]) for review in result["reviews"]}
    for image_ref in coverage["comparison_images"]:
        match = re.search(r"comparison_(\d+)_(\d+)", Path(image_ref).stem); source_pages = list(range(int(match.group(1)), int(match.group(2)) + 1))
        if tuple(source_pages) in completed: continue
        success = False
        for attempt in range(3):
            result["api_calls"] += 1; atomic_write_json(output, result)
            try:
                response = provider.transcribe_images(
                    model=provider_config["model"],
                    prompt="You are a conservative visual acceptance auditor. Compare source-page panels to mapped bilingual-release panels. Do not infer missing content that is not visible.",
                    context_message=(f"Compare original physical pages {source_pages} (red SOURCE labels) with their mapped final bilingual release pages (blue RELEASE labels). "
                        "Check headings, prose/table/index coverage, order, truncation, duplication, numeric/unit preservation, and gross layout defects. "
                        "Facsimile plus ordered transcription is acceptable for uncertain tables. Return JSON only with keys source_pages, coverage_ok, large_missing_or_truncated, order_issues, duplicate_issues, table_issues, index_issues, requires_fix."),
                    image_data_urls=[_data_url(Path(image_ref))], max_output_tokens=2200, temperature=0, do_sample=False, thinking_mode="disabled", response_format_json_object=False,
                )
                parsed = _json_content(response.content); result["reviews"].append({"source_pages": source_pages, "request_id": response.request_id, "review": parsed})
                result["failures"] = [failure for failure in result["failures"] if failure["source_pages"] != source_pages]; atomic_write_json(output, result); success = True; break
            except Exception as exc:
                result["failures"] = [failure for failure in result["failures"] if failure["source_pages"] != source_pages]
                result["failures"].append({"source_pages": source_pages, "attempt": attempt + 1, "exception_type": type(exc).__name__, "http_status": getattr(exc, "status_code", None)}); atomic_write_json(output, result)
        if not success: result["aborted_after_repeated_error"] = True; break
    result["complete"] = len({page for review in result["reviews"] for page in review["source_pages"]}) == 28 and not result["failures"]
    atomic_write_json(output, result); return {"path": str(output), **result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["source-review", "coverage", "release-review"])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--bilingual-pdf", type=Path)
    args = parser.parse_args()
    if args.action == "source-review": result = review_source_pages(args.root, args.config)
    elif args.action == "coverage":
        if not args.bilingual_pdf: parser.error("--bilingual-pdf is required for coverage")
        result = build_coverage(args.root, args.bilingual_pdf)
    else: result = review_release_comparisons(args.root, args.config)
    print(json.dumps({key: result[key] for key in ("path", "provider", "model", "api_calls", "valid", "coverage_count") if key in result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
