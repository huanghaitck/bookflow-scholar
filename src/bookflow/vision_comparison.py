"""Offline QA comparison between one visual result and the PDF text layer."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from pathlib import Path

import fitz
from pydantic import BaseModel, Field

from .io_utils import atomic_write_text, load_json
from .paths import ProjectSettings, project_root, resolve_project_path
from .schemas import VisionPageResult


class PageComparison(BaseModel):
    pdf_page: int
    comparison_source: str
    schema_validation_valid: bool
    glm_body_characters: int
    pdf_text_layer_characters: int
    normalized_similarity_ratio: float
    possible_missing_fragments: list[str] = Field(default_factory=list)
    possible_added_fragments: list[str] = Field(default_factory=list)
    running_header_in_body: bool
    page_number_in_body: bool
    paragraph_order_reasonable: bool
    number_tokens_only_in_glm: list[str] = Field(default_factory=list)
    number_tokens_only_in_pdf: list[str] = Field(default_factory=list)
    capitalized_tokens_only_in_glm: list[str] = Field(default_factory=list)
    capitalized_tokens_only_in_pdf: list[str] = Field(default_factory=list)
    punctuation_difference_count: int
    line_end_hyphen_difference_count: int
    continuation_from_previous: bool | None = None
    continuation_to_next: bool | None = None
    boundary_notes: str | None = None
    requires_human_review: bool = True


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _limited_fragments(
    matcher: difflib.SequenceMatcher[str], first: str, second: str, opcode: str
) -> list[str]:
    fragments: list[str] = []
    for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
        if tag not in {opcode, "replace"}:
            continue
        value = first[first_start:first_end] if opcode == "delete" else second[second_start:second_end]
        value = value.strip()
        if value:
            fragments.append(value[:160])
        if len(fragments) >= 12:
            break
    return fragments


def compare_page_with_text_layer(
    normalized_path: str | Path,
    pdf_path: str | Path,
    page: int,
    settings: ProjectSettings,
    *,
    root: Path | None = None,
    raw_response_path: str | Path | None = None,
) -> PageComparison:
    root = (root or project_root()).resolve()
    source = resolve_project_path(pdf_path, root=root)
    protected = resolve_project_path(settings.source_pdf, root=root)
    if source == protected:
        raise PermissionError("Phase 2A comparison cannot process the configured full PDF")
    result = VisionPageResult.model_validate(load_json(normalized_path))
    if result.pdf_page != page:
        raise ValueError("Normalized result page does not match requested comparison page")
    with fitz.open(source) as document:
        if page < 1 or page > document.page_count:
            raise ValueError("Comparison page is outside the PDF page range")
        pdf_text = document.load_page(page - 1).get_text("text") or ""
    comparison_source = "normalized_schema_validated"
    schema_validation_valid = True
    if result.blocks:
        body_texts = [block.text for block in result.blocks if block.block_type == "body"]
        orders = [block.order for block in result.blocks if block.block_type == "body"]
        running_header = result.running_header or ""
        page_number_text = result.page_number_text or ""
        continuation_from_previous = result.continuation_from_previous
        continuation_to_next = result.continuation_to_next
        boundary_notes = result.boundary_notes
    else:
        if raw_response_path is None:
            raise ValueError("Normalized result has no blocks; a preserved raw response is required")
        raw = load_json(raw_response_path)
        content = raw["response"]["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Raw provider response contains no string model content")
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines.pop()
            candidate = "\n".join(lines).strip()
        payload = json.loads(candidate)
        if not isinstance(payload, dict) or payload.get("pdf_page") != page:
            raise ValueError("Raw model payload does not match the comparison page")
        blocks = payload.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError("Raw model payload has no block list")
        body_texts = [
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("block_type") == "body"
            and isinstance(block.get("text"), str)
        ]
        orders = [
            block.get("order")
            for block in blocks
            if isinstance(block, dict) and block.get("block_type") == "body"
        ]
        running_header = payload.get("running_header") or ""
        page_number_text = str(payload.get("page_number_text") or "")
        continuation_from_previous = payload.get("continuation_from_previous")
        continuation_to_next = payload.get("continuation_to_next")
        boundary_notes = payload.get("boundary_notes")
        comparison_source = "preserved_raw_unvalidated_model_content"
        schema_validation_valid = False
    glm_text = "\n\n".join(body_texts)
    glm_normalized = _normalize(glm_text)
    pdf_normalized = _normalize(pdf_text)
    matcher = difflib.SequenceMatcher(None, pdf_normalized, glm_normalized, autojunk=False)
    number_glm = set(re.findall(r"\b\d+(?:[.,]\d+)*\b", glm_text))
    number_pdf = set(re.findall(r"\b\d+(?:[.,]\d+)*\b", pdf_text))
    caps_glm = set(re.findall(r"\b[A-Z][A-Za-z'-]{2,}\b", glm_text))
    caps_pdf = set(re.findall(r"\b[A-Z][A-Za-z'-]{2,}\b", pdf_text))
    punctuation = set(".,;:!?—–-()[]{}\"'“”‘’")
    glm_punctuation_count = sum(character in punctuation for character in glm_text)
    pdf_punctuation_count = sum(character in punctuation for character in pdf_text)
    glm_line_hyphens = sum(line.rstrip().endswith("-") for line in glm_text.splitlines())
    pdf_line_hyphens = sum(line.rstrip().endswith("-") for line in pdf_text.splitlines())
    body_folded = glm_normalized.casefold()
    header = _normalize(running_header).casefold()
    page_number = _normalize(page_number_text).casefold()
    return PageComparison(
        pdf_page=page,
        comparison_source=comparison_source,
        schema_validation_valid=schema_validation_valid,
        glm_body_characters=len(glm_text),
        pdf_text_layer_characters=len(pdf_text),
        normalized_similarity_ratio=round(matcher.ratio(), 6),
        possible_missing_fragments=_limited_fragments(
            matcher, pdf_normalized, glm_normalized, "delete"
        ),
        possible_added_fragments=_limited_fragments(
            matcher, pdf_normalized, glm_normalized, "insert"
        ),
        running_header_in_body=bool(header and header in body_folded),
        page_number_in_body=bool(page_number and page_number in body_folded),
        paragraph_order_reasonable=orders == sorted(set(orders)),
        number_tokens_only_in_glm=sorted(number_glm - number_pdf),
        number_tokens_only_in_pdf=sorted(number_pdf - number_glm),
        capitalized_tokens_only_in_glm=sorted(caps_glm - caps_pdf)[:30],
        capitalized_tokens_only_in_pdf=sorted(caps_pdf - caps_glm)[:30],
        punctuation_difference_count=abs(
            glm_punctuation_count - pdf_punctuation_count
        ),
        line_end_hyphen_difference_count=abs(glm_line_hyphens - pdf_line_hyphens),
        continuation_from_previous=continuation_from_previous,
        continuation_to_next=continuation_to_next,
        boundary_notes=boundary_notes,
        requires_human_review=True,
    )


def write_comparison_report(
    comparison: PageComparison,
    output_path: str | Path,
    *,
    normalized_path: str | Path,
) -> None:
    lines = [
        "# Phase 2A 单页视觉结果与 PDF 文字层比较",
        "",
        f"PDF页码：{comparison.pdf_page}",
        f"规范化视觉结果：`{Path(normalized_path)}`",
        f"比较所用视觉文本来源：`{comparison.comparison_source}`",
        f"Schema校验通过：{'是' if comparison.schema_validation_valid else '否'}",
        "",
        "> 本比较只用于发现差异。PDF文字层和GLM结果都不是绝对权威，程序没有互相覆盖或自动修正文案。",
        "",
        "## 汇总",
        "",
        f"- GLM正文字符数：{comparison.glm_body_characters}",
        f"- PDF文字层字符数：{comparison.pdf_text_layer_characters}",
        f"- 标准化相似度：{comparison.normalized_similarity_ratio:.4f}",
        f"- 页眉疑似混入正文：{'是' if comparison.running_header_in_body else '否'}",
        f"- 页码疑似混入正文：{'是' if comparison.page_number_in_body else '否'}",
        f"- 段落顺序初步合理：{'是' if comparison.paragraph_order_reasonable else '否'}",
        f"- 标点数量差：{comparison.punctuation_difference_count}",
        f"- 行末连字符数量差：{comparison.line_end_hyphen_difference_count}",
        f"- continuation_from_previous：{comparison.continuation_from_previous}",
        f"- continuation_to_next：{comparison.continuation_to_next}",
        f"- boundary_notes：{comparison.boundary_notes}",
        "",
        "## 待人工核对",
        "",
        f"- 可能遗漏片段：{comparison.possible_missing_fragments}",
        f"- 可能新增片段：{comparison.possible_added_fragments}",
        f"- 仅GLM出现的数字：{comparison.number_tokens_only_in_glm}",
        f"- 仅文字层出现的数字：{comparison.number_tokens_only_in_pdf}",
        f"- 仅GLM出现的首字母大写词：{comparison.capitalized_tokens_only_in_glm}",
        f"- 仅文字层出现的首字母大写词：{comparison.capitalized_tokens_only_in_pdf}",
        "",
        "结论：所有差异保留待人工复核；没有自动修改原始响应、规范化结果或PDF文字层。",
        "",
    ]
    atomic_write_text(output_path, "\n".join(lines))
