"""Typed, auditable web-assist package export and return workflow."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import fitz


WebAssistPackageType = Literal["glossary_review", "difficult_pages"]
GLOSSARY_PROMPT_ID = "bookflow-glossary-review-v1"
DIFFICULT_PROMPT_ID = "bookflow-difficult-page-review-v1"
ALLOWED_IMPORT_SUFFIXES = {".json", ".csv", ".xlsx", ".md", ".txt", ".zip"}
FORBIDDEN_IMPORT_SUFFIXES = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".js", ".vbs", ".msi", ".xlsm"}
GLOSSARY_EDITABLE_FIELDS = {"user_final_translation", "preserve_original", "user_note", "review_status"}
DIFFICULT_EDITABLE_FIELDS = {
    "object_corrections", "user_corrected_text", "user_corrected_markdown",
    "user_structure_note", "review_status",
}
DIFFICULT_OBJECT_EDITABLE_FIELDS = {
    "corrected_source_text", "corrected_translated_text", "structure_note", "review_status",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(_sha256(item).encode("ascii"))
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


def _column_name(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _write_xlsx(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Write a deterministic, macro-free single-sheet XLSX without new dependencies."""
    sheet_rows: list[str] = []
    values = [dict(zip(columns, columns)), *rows]
    for row_number, row in enumerate(values, start=1):
        cells: list[str] = []
        for column_number, name in enumerate(columns, start=1):
            raw = row.get(name, "")
            if isinstance(raw, (list, dict)):
                raw = json.dumps(raw, ensure_ascii=False)
            elif isinstance(raw, bool):
                raw = "true" if raw else "false"
            text = escape("" if raw is None else str(raw))
            reference = f"{_column_name(column_number)}{row_number}"
            cells.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="WebAssist" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>',
        "xl/worksheets/sheet1.xml": worksheet,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content.encode("utf-8"))


def _read_xlsx(path: Path) -> list[dict[str, str]]:
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("s:si", namespace):
                shared.append("".join(node.text or "" for node in item.findall(".//s:t", namespace)))
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    table: list[list[str]] = []
    for row in sheet.findall(".//s:row", namespace):
        values: list[str] = []
        for cell in row.findall("s:c", namespace):
            reference = cell.attrib.get("r", "A1")
            letters = "".join(character for character in reference if character.isalpha())
            column = 0
            for character in letters:
                column = column * 26 + ord(character.upper()) - 64
            while len(values) < column:
                values.append("")
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//s:t", namespace))
            else:
                node = cell.find("s:v", namespace)
                value = node.text if node is not None and node.text is not None else ""
                if cell_type == "s" and value:
                    value = shared[int(value)]
            values[column - 1] = value
        table.append(values)
    if not table:
        return []
    headers = table[0]
    return [{header: row[index] if index < len(row) else "" for index, header in enumerate(headers)} for row in table[1:]]


@dataclass
class WebAssistConflict:
    conflict_type: str
    item_id: str | None
    old_value: Any = None
    imported_value: Any = None
    current_value: Any = None
    recommended_action: str = "review"


@dataclass
class WebAssistDiff:
    item_id: str
    field: str
    old_value: Any
    imported_value: Any
    current_value: Any


@dataclass
class GlossaryReviewItem:
    package_id: str
    term_id: str
    project_id: str
    source_document_id: str
    occurrence_id: str
    translation_unit_id: str
    source_object_id: str
    source_term: str
    normalized_term: str
    detected_language: str
    page_number: int | None
    segment_id: str
    context_before: str
    context_current: str
    context_after: str
    current_translation: str
    source_span_start: int
    source_span_end: int
    translated_span_start: int | None
    translated_span_end: int | None
    unit_source_sha256: str
    unit_translation_sha256: str
    candidate_translations: list[str]
    confidence: float
    uncertainty_reason: str
    occurrence_count: int
    suggested_action: str
    user_final_translation: str = ""
    preserve_original: bool = False
    user_note: str = ""
    review_status: str = "pending"


@dataclass
class GlossaryReviewPackage:
    package_id: str
    package_type: WebAssistPackageType
    project_id: str
    source_document_id: str
    source_hash: str
    items: list[GlossaryReviewItem]


@dataclass
class GlossaryImportRow:
    package_id: str
    term_id: str
    user_final_translation: str = ""
    preserve_original: bool = False
    user_note: str = ""
    review_status: str = "pending"


@dataclass
class DifficultPageItem:
    package_id: str
    page_item_id: str
    project_id: str
    source_document_id: str
    page_number: int
    page_image: str
    source_hash: str
    current_ocr: str
    current_structure: str
    current_markdown: str
    issue_codes: list[str]
    confidence: float
    model_notes: str
    suggested_task: str
    objects: list[dict[str, Any]] = field(default_factory=list)
    object_corrections: list[dict[str, Any]] = field(default_factory=list)
    user_corrected_text: str = ""
    user_corrected_markdown: str = ""
    user_structure_note: str = ""
    review_status: str = "pending"


@dataclass
class DifficultPagePackage:
    package_id: str
    package_type: WebAssistPackageType
    project_id: str
    source_document_id: str
    source_hash: str
    items: list[DifficultPageItem]


@dataclass
class DifficultPageImportResult:
    package_id: str
    valid: bool
    imported_items: int
    conflicts: list[WebAssistConflict] = field(default_factory=list)


@dataclass
class WebAssistPackage:
    package_id: str
    package_type: WebAssistPackageType
    project_id: str
    source_document_id: str
    source_hash: str
    status: str
    item_count: int
    export_path: str
    archive_path: str
    created_at: str
    updated_at: str


@dataclass
class WebAssistExportRequest:
    package_type: WebAssistPackageType
    project_id: str
    source_document_id: str | None = None


@dataclass
class WebAssistExportResult:
    package: WebAssistPackage | None
    files: list[str]
    skipped: bool = False
    reason: str | None = None


@dataclass
class WebAssistImportRequest:
    package_id: str
    import_path: str
    source_document_id: str


@dataclass
class WebAssistImportValidation:
    package_id: str
    valid: bool
    import_sha256: str
    changes: list[WebAssistDiff]
    conflicts: list[WebAssistConflict]
    imported_items: int


@dataclass
class WebAssistApplyResult:
    application_id: str
    package_id: str
    applied_items: int
    conflicts: int
    affected_outputs: list[str]
    incremental_rebuild: bool
    undo_available: bool
    invalidated_units: list[str] = field(default_factory=list)
    rebuild_required: bool = True


def _official_prompt(package_type: WebAssistPackageType) -> tuple[str, str]:
    if package_type == "glossary_review":
        return GLOSSARY_PROMPT_ID, """# Bookflow official glossary-review prompt

You are reviewing terminology for one explicitly identified Bookflow source document.
Use the supplied context and language pair. Treat every row as one occurrence, not as
permission to perform a document-wide string replacement.

Rules:
1. Edit only user_final_translation, preserve_original, user_note, and review_status.
2. Never alter package_id, project_id, source_document_id, occurrence_id,
   translation_unit_id, source_object_id, spans, hashes, or source text.
3. Keep uncertain rows pending. Never invent identifiers or context.
4. Preserve names, numbers, note markers, and authorial meaning.
5. Return the same JSON, XLSX, or CSV structure without macros or executable content.
"""
    return DIFFICULT_PROMPT_ID, """# Bookflow official difficult-page prompt

You are correcting one explicitly identified Bookflow source document. Use the page
image, OCR, layout objects, object IDs, element types, bounding boxes, and notes.

Rules:
1. Correct individual objects only through object_corrections.
2. Each correction must retain its source_object_id and translation_unit_id.
3. Edit only corrected_source_text, corrected_translated_text, structure_note, and
   review_status inside an object correction.
4. Never flatten the whole page, delete unrelated objects, or mix headers, footers,
   footnotes, endnotes, captions, tables, and body prose.
5. If an object cannot be resolved, mark it needs_human_review.
6. Whole-page free text may be supplied for preview, but Bookflow will not apply it.
7. Return the same JSON, XLSX, or CSV structure without macros or executable content.
"""


def _human_instructions(package_type: WebAssistPackageType) -> str:
    label = "术语表" if package_type == "glossary_review" else "疑难页"
    return f"""# Bookflow {label}审校包

1. 将本目录中指定的 JSON/XLSX/CSV、页面图片和 `OFFICIAL_PROMPT.md` 上传给网页 AI。
2. 复制官方提示词并让模型严格保持 ID、哈希和结构不变。
3. 下载模型返回的文件，不要运行脚本、宏或可执行程序。
4. 回到 Bookflow，选择返回文件或整个审校包目录。
5. 先校验并预览差异；确认 Source 与修改对象正确后再应用。

无法确定的内容请保持 pending，疑难页整页自由文本不会被自动应用。
"""


def _language_key(language: str) -> str:
    return str(language or "en").strip().replace("_", "-").lower().split("-", 1)[0] or "en"


def _safe_package_filename(value: str, *, fallback: str = "book") -> str:
    cleaned = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', " ", str(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or fallback)[:100].rstrip(" .") or fallback


def _localized_official_prompt(
    package_type: WebAssistPackageType,
    target_language: str,
) -> tuple[str, str]:
    language = _language_key(target_language)
    glossary = {
        "zh": """# Bookflow 官方术语审校提示词

请使用随包上下文审校这个明确指定的 Source。每一行只代表一次具体出现，不得据此执行整书字符串替换。一般使用文本模型即可；只有术语判断依赖图表、图注或页面视觉信息时才使用多模态模型。

只能编辑 user_final_translation、preserve_original、user_note、review_status。不得修改任何项目、Source、出现位置、翻译单元、对象、跨度、哈希或原文标识。无法确定的项目保持 pending。按原 JSON、XLSX 或 CSV 结构返回，不得加入宏或可执行内容。
""",
        "en": """# Bookflow official glossary-review prompt

Review the explicitly identified Source using the supplied context. Each row is one occurrence and never authorizes a document-wide string replacement. A text model is normally sufficient; use a multimodal model only when a decision depends on a figure, caption, or other visual evidence.

Edit only user_final_translation, preserve_original, user_note, and review_status. Never alter project, Source, occurrence, translation-unit, object, span, hash, or source-text identifiers. Keep uncertain rows pending. Return the same JSON, XLSX, or CSV structure without macros or executable content.
""",
        "fr": """# Invite officielle Bookflow pour la révision terminologique

Révisez le Source explicitement indiqué avec le contexte fourni. Chaque ligne correspond à une occurrence précise et n’autorise jamais un remplacement global. Un modèle texte suffit normalement ; utilisez un modèle multimodal seulement si la décision dépend d’une figure, d’une légende ou d’un autre indice visuel.

Modifiez uniquement user_final_translation, preserve_original, user_note et review_status. Ne modifiez aucun identifiant de projet, Source, occurrence, unité, objet, segment, hachage ou texte source. Laissez pending les éléments incertains. Renvoyez la même structure JSON, XLSX ou CSV, sans macro ni contenu exécutable.
""",
        "de": """# Offizieller Bookflow-Prompt zur Terminologieprüfung

Prüfe die Terminologie der ausdrücklich angegebenen Quelldatei anhand des Kontexts. Jede Zeile bezeichnet genau ein Vorkommen und erlaubt keine globale Textersetzung. Normalerweise genügt ein Textmodell; ein multimodales Modell ist nur nötig, wenn Abbildungen, Bildtexte oder andere visuelle Hinweise entscheidend sind.

Bearbeite nur user_final_translation, preserve_original, user_note und review_status. Ändere keine Projekt-, Quellen-, Vorkommens-, Einheiten-, Objekt-, Bereichs-, Hash- oder Quelltext-IDs. Unsichere Einträge bleiben pending. Gib dieselbe JSON-, XLSX- oder CSV-Struktur ohne Makros oder ausführbare Inhalte zurück.
""",
        "ja": """# Bookflow 公式用語レビュー用プロンプト

提供された文脈を使い、明示された Source の用語を確認してください。各行は一つの出現箇所だけを表し、文書全体の置換を許可しません。通常はテキストモデルで十分です。図、キャプション、その他の視覚情報が判断に必要な場合だけマルチモーダルモデルを使用してください。

user_final_translation、preserve_original、user_note、review_status だけを編集します。プロジェクト、Source、出現箇所、翻訳単位、オブジェクト、範囲、ハッシュ、原文の識別子は変更しません。不確かな項目は pending のままにし、同じ JSON、XLSX、CSV 構造で返してください。
""",
        "es": """# Prompt oficial de Bookflow para revisar terminología

Revise la terminología del Source indicado con el contexto proporcionado. Cada fila representa una aparición concreta y no autoriza un reemplazo global. Normalmente basta un modelo de texto; use uno multimodal solo si la decisión depende de figuras, leyendas u otra evidencia visual.

Edite solo user_final_translation, preserve_original, user_note y review_status. No cambie identificadores de proyecto, Source, aparición, unidad, objeto, intervalo, hash ni texto fuente. Mantenga pending los elementos inciertos. Devuelva la misma estructura JSON, XLSX o CSV, sin macros ni contenido ejecutable.
""",
    }
    difficult = {
        "zh": """# Bookflow 官方疑难页审校提示词

必须使用支持图片输入的视觉/多模态模型。请同时检查页面图片、OCR、版面对象、对象 ID、元素类型、边界框和说明，不得只根据抽取文本判断。

只能通过 object_corrections 修正单个对象，并保留 source_object_id 和 translation_unit_id。对象内只能编辑 corrected_source_text、corrected_translated_text、structure_note、review_status。不得把整页压平、删除无关对象或混合页眉、页脚、脚注、尾注、图注、表格和正文。无法解决的对象标记为 needs_human_review；整页自由文本只能预览。按原结构返回，不得加入可执行内容。
""",
        "en": """# Bookflow official difficult-page prompt

Use a vision-capable multimodal model. Inspect the page image together with OCR, layout objects, object IDs, element types, bounding boxes, and notes; do not decide from extracted text alone.

Correct individual objects only through object_corrections and retain source_object_id and translation_unit_id. Edit only corrected_source_text, corrected_translated_text, structure_note, and review_status. Never flatten a page, delete unrelated objects, or mix headers, footers, footnotes, endnotes, captions, tables, and body prose. Mark unresolved objects needs_human_review; whole-page free text is preview-only. Return the same structure without executable content.
""",
        "fr": """# Invite officielle Bookflow pour les pages difficiles

Utilisez un modèle multimodal acceptant les images. Examinez ensemble l’image de page, l’OCR, les objets de mise en page, leurs identifiants, types, cadres et notes ; ne décidez pas à partir du seul texte extrait.

Corrigez les objets uniquement dans object_corrections en conservant source_object_id et translation_unit_id. Modifiez seulement corrected_source_text, corrected_translated_text, structure_note et review_status. N’aplatissez pas la page et ne mélangez pas en-têtes, pieds, notes, légendes, tableaux et corps du texte. Marquez needs_human_review les objets non résolus ; le texte libre de page entière sert uniquement à l’aperçu.
""",
        "de": """# Offizieller Bookflow-Prompt für schwierige Seiten

Verwende ein bildfähiges multimodales Modell. Prüfe Seitenbild, OCR, Layoutobjekte, Objekt-IDs, Elementtypen, Begrenzungsrahmen und Hinweise gemeinsam; entscheide nicht nur anhand des extrahierten Textes.

Korrigiere einzelne Objekte nur in object_corrections und behalte source_object_id sowie translation_unit_id bei. Bearbeite nur corrected_source_text, corrected_translated_text, structure_note und review_status. Verflache keine Seite und vermische keine Kopf- oder Fußzeilen, Fuß- oder Endnoten, Bildtexte, Tabellen und Fließtexte. Markiere ungelöste Objekte mit needs_human_review; Ganzseiten-Freitext dient nur der Vorschau.
""",
        "ja": """# Bookflow 公式難ページレビュー用プロンプト

画像入力に対応する視覚・マルチモーダルモデルを必ず使用してください。ページ画像、OCR、レイアウトオブジェクト、オブジェクト ID、要素種別、境界ボックス、注記を一緒に確認し、抽出テキストだけで判断しないでください。

object_corrections 内で個別オブジェクトだけを修正し、source_object_id と translation_unit_id を保持します。編集できるのは corrected_source_text、corrected_translated_text、structure_note、review_status だけです。ページを平坦化せず、ヘッダー、フッター、脚注、後注、キャプション、表、本文を混在させません。未解決は needs_human_review とし、ページ全体の自由文はプレビュー専用です。
""",
        "es": """# Prompt oficial de Bookflow para páginas difíciles

Use obligatoriamente un modelo multimodal que acepte imágenes. Examine en conjunto la imagen, el OCR, los objetos de diseño, sus identificadores, tipos, cuadros y notas; no decida solo con el texto extraído.

Corrija objetos individuales únicamente mediante object_corrections y conserve source_object_id y translation_unit_id. Edite solo corrected_source_text, corrected_translated_text, structure_note y review_status. No aplane la página ni mezcle encabezados, pies, notas, leyendas, tablas y texto principal. Marque needs_human_review los objetos sin resolver; el texto libre de página completa es solo para vista previa.
""",
    }
    prompt_id = GLOSSARY_PROMPT_ID if package_type == "glossary_review" else DIFFICULT_PROMPT_ID
    prompts = glossary if package_type == "glossary_review" else difficult
    return prompt_id, prompts.get(language, prompts["en"])


def _localized_human_instructions(
    package_type: WebAssistPackageType,
    target_language: str,
) -> str:
    language = _language_key(target_language)
    model = {
        "zh": "疑难页必须选择支持图片的多模态模型；术语表通常使用文本模型即可。",
        "en": "Use an image-capable multimodal model for difficult pages; a text model is normally sufficient for glossary review.",
        "fr": "Utilisez un modèle multimodal acceptant les images pour les pages difficiles ; un modèle texte suffit normalement pour la terminologie.",
        "de": "Für schwierige Seiten ist ein bildfähiges multimodales Modell erforderlich; für die Terminologie genügt normalerweise ein Textmodell.",
        "ja": "難ページには画像対応のマルチモーダルモデルを使用し、用語レビューには通常テキストモデルを使用します。",
        "es": "Use un modelo multimodal con imágenes para páginas difíciles; normalmente basta un modelo de texto para la terminología.",
    }
    steps = {
        "zh": "将本目录中已经生成的 ZIP 上传给网页 AI，并使用 OFFICIAL_PROMPT.md。下载返回结果后，在 Bookflow 中先校验并预览差异，再确认应用。无法确定的内容保持 pending。",
        "en": "Upload the generated ZIP to the web AI and use OFFICIAL_PROMPT.md. After downloading the result, validate and preview the diff in Bookflow before applying it. Keep uncertain items pending.",
        "fr": "Téléversez le ZIP généré vers l’IA web et utilisez OFFICIAL_PROMPT.md. Après le téléchargement du résultat, validez-le et prévisualisez les différences dans Bookflow avant application. Laissez pending les éléments incertains.",
        "de": "Lade die erzeugte ZIP-Datei zum Web-KI-Dienst hoch und verwende OFFICIAL_PROMPT.md. Prüfe danach das Ergebnis in Bookflow und zeige die Änderungen vor dem Anwenden an. Unsichere Einträge bleiben pending.",
        "ja": "生成済み ZIP を Web AI にアップロードし、OFFICIAL_PROMPT.md を使用してください。返却結果は Bookflow で検証し、差分を確認してから適用します。不確かな項目は pending のままにします。",
        "es": "Suba el ZIP generado a la IA web y use OFFICIAL_PROMPT.md. Al descargar el resultado, valídelo y previsualice las diferencias en Bookflow antes de aplicarlo. Mantenga pending los elementos inciertos.",
    }
    return f"# Bookflow Web Assist\n\n{model.get(language, model['en'])}\n\n{steps.get(language, steps['en'])}\n"


class WebAssistService:
    """File-backed service shared by glossary and difficult-page package variants."""

    def __init__(self, backend_root: Path) -> None:
        self.backend_root = backend_root.resolve()
        self.root = backend_root / "web_assist"
        self.state_root = self.root / "state"
        self.export_root = self.root / "exports"
        self.revision_root = self.root / "revisions"
        self.audit_path = self.root / "web_assist_audit.jsonl"
        for path in (self.state_root, self.export_root, self.revision_root):
            path.mkdir(parents=True, exist_ok=True)

    def create_package(
        self,
        request: WebAssistExportRequest,
        sources: list[dict[str, Any]],
        jobs: list[dict[str, Any]],
    ) -> WebAssistExportResult:
        if request.package_type not in {"glossary_review", "difficult_pages"}:
            raise ValueError("unsupported web-assist package type")
        if not request.source_document_id:
            raise ValueError("active source document must be selected explicitly")
        selected = next((item for item in sources if item["source_id"] == request.source_document_id), None)
        if selected is None:
            raise ValueError("selected source document does not belong to the active project")
        package_id = _id(f"webassist_{request.package_type}")
        export_path = self.export_root / package_id
        export_path.mkdir(parents=True, exist_ok=False)
        output_paths = [
            str(item["output_path"]) for item in jobs if item.get("output_path")
            and self._output_matches_source(Path(str(item["output_path"])), selected["sha256"])
        ]
        source_language = str(selected.get("source_language") or "auto")
        target_language = "en"
        for output_value in reversed(output_paths):
            metadata_path = Path(output_value) / "metadata.json"
            if not metadata_path.is_file():
                continue
            metadata = json.loads(metadata_path.read_text("utf-8"))
            source_language = str(metadata.get("source_language") or source_language)
            target_language = str(metadata.get("target_language") or target_language)
            break
        if target_language == "en":
            for job in reversed(jobs):
                if job.get("source_id") != selected["source_id"]:
                    continue
                config = json.loads(job.get("config_json") or "{}")
                target_language = str(config.get("target_language") or target_language)
                source_language = str(config.get("source_language") or source_language)
                break
        if request.package_type == "glossary_review":
            items = self._glossary_items(package_id, request.project_id, selected, output_paths)
        else:
            items = self._difficult_items(package_id, request.project_id, selected, output_paths, export_path)
        if not items:
            export_path.rmdir()
            return WebAssistExportResult(
                package=None,
                files=[],
                skipped=True,
                reason=("no_glossary_items" if request.package_type == "glossary_review"
                        else "no_difficult_pages"),
            )
        if request.package_type == "glossary_review":
            self._export_glossary(
                export_path, package_id, request.project_id, selected, items,
                source_language=source_language, target_language=target_language,
            )
        else:
            self._export_difficult(
                export_path, package_id, request.project_id, selected, items,
                source_language=source_language, target_language=target_language,
            )
        source_stem = Path(str(selected.get("filename") or selected["source_path"])).stem
        package_label = "glossary" if request.package_type == "glossary_review" else "difficult-pages"
        archive_name = _safe_package_filename(
            f"{source_stem}_{_language_key(target_language)}_{package_label}"
        ) + ".zip"
        archive_path = export_path / archive_name
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(path for path in export_path.rglob("*") if path.is_file()):
                if file_path != archive_path:
                    archive.write(file_path, file_path.relative_to(export_path).as_posix())
        now = _now()
        package = WebAssistPackage(
            package_id=package_id,
            package_type=request.package_type,
            project_id=request.project_id,
            source_document_id=selected["source_id"],
            source_hash=selected["sha256"],
            status="exported",
            item_count=len(items),
            export_path=str(export_path),
            archive_path=str(archive_path),
            created_at=now,
            updated_at=now,
        )
        state = asdict(package) | {
            "items": items,
            "source_path": selected["source_path"],
            "source_language": source_language,
            "target_language": target_language,
            "output_paths": output_paths,
            "validations": [],
            "applications": [],
        }
        _write_json(self._state_path(package_id), state)
        self._audit(package_id, request.package_type, "export", item_count=len(items), affected_outputs=output_paths)
        return WebAssistExportResult(
            package=package,
            files=[str(path) for path in sorted(export_path.rglob("*")) if path.is_file()],
        )

    def list_packages(self, project_id: str | None = None,
                      source_document_id: str | None = None) -> list[dict[str, Any]]:
        packages: list[dict[str, Any]] = []
        for path in sorted(self.state_root.glob("*.json")):
            value = json.loads(path.read_text("utf-8"))
            if project_id and value["project_id"] != project_id:
                continue
            if source_document_id and value["source_document_id"] != source_document_id:
                continue
            packages.append({
                key: value.get(key, "")
                for key in WebAssistPackage.__dataclass_fields__
            })
        return packages

    def get_package(self, package_id: str) -> dict[str, Any]:
        state = self._load_state(package_id)
        prompt_path = Path(state["export_path"]) / "OFFICIAL_PROMPT.md"
        return {
            **state,
            "official_prompt": prompt_path.read_text("utf-8") if prompt_path.is_file() else "",
        }

    @staticmethod
    def _output_matches_source(output: Path, source_hash: str) -> bool:
        metadata_path = output / "metadata.json"
        if not metadata_path.is_file():
            return False
        try:
            metadata = json.loads(metadata_path.read_text("utf-8"))
            workspace = Path(str(metadata.get("workspace") or ""))
            manifest_path = workspace / "bookflow_workspace.json"
            if not manifest_path.is_file():
                return False
            manifest = json.loads(manifest_path.read_text("utf-8"))
            return manifest.get("source_pdf_sha256") == source_hash
        except (OSError, ValueError):
            return False

    def _workspace_from_outputs(self, source_hash: str, output_paths: list[str]) -> Path:
        return self._workspace_for_state({"source_hash": source_hash, "output_paths": output_paths})

    def _workspace_for_state(self, state: dict[str, Any]) -> Path:
        """Resolve the scoped production workspace through its canonical output metadata."""
        workspace_root = (self.backend_root / "workspaces").resolve()
        for output_value in reversed(state.get("output_paths", [])):
            metadata_path = Path(str(output_value)) / "metadata.json"
            if not metadata_path.is_file():
                continue
            metadata = json.loads(metadata_path.read_text("utf-8"))
            workspace_value = metadata.get("workspace")
            if not workspace_value:
                continue
            workspace = Path(str(workspace_value)).resolve()
            try:
                workspace.relative_to(workspace_root)
            except ValueError:
                continue
            manifest_path = workspace / "bookflow_workspace.json"
            units_path = workspace / "data/translation_units.jsonl"
            if not manifest_path.is_file() or not units_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text("utf-8"))
            if manifest.get("source_pdf_sha256") != state.get("source_hash"):
                continue
            return workspace
        raise RuntimeError("scoped production workspace is unavailable for incremental rebuild")

    def validate_import(self, request: WebAssistImportRequest) -> WebAssistImportValidation:
        state = self._load_state(request.package_id)
        if request.source_document_id != state["source_document_id"]:
            raise ValueError("web-assist package does not belong to the active source document")
        import_path = Path(request.import_path).resolve()
        self._validate_import_surface(import_path)
        imported = self._load_import_rows(import_path, state["package_type"])
        rows = imported.get("items", imported) if isinstance(imported, dict) else imported
        if not isinstance(rows, list):
            raise ValueError("import payload must contain an items array")
        id_field = "term_id" if state["package_type"] == "glossary_review" else "page_item_id"
        editable = GLOSSARY_EDITABLE_FIELDS if state["package_type"] == "glossary_review" else DIFFICULT_EDITABLE_FIELDS
        original = {item[id_field]: item for item in state["items"]}
        seen: set[str] = set()
        conflicts: list[WebAssistConflict] = []
        changes: list[WebAssistDiff] = []
        for row in rows:
            item_id = str(row.get(id_field, ""))
            if row.get("package_id") != request.package_id:
                conflicts.append(WebAssistConflict("wrong_package", item_id or None, imported_value=row.get("package_id"), current_value=request.package_id, recommended_action="reject"))
            if not item_id or item_id not in original:
                conflicts.append(WebAssistConflict("unknown_item", item_id or None, imported_value=item_id, recommended_action="reject"))
                continue
            if item_id in seen:
                conflicts.append(WebAssistConflict("duplicate_item", item_id, recommended_action="deduplicate"))
                continue
            seen.add(item_id)
            if state["package_type"] == "difficult_pages" and row.get("source_hash") != state["source_hash"]:
                conflicts.append(WebAssistConflict("source_changed", item_id, imported_value=row.get("source_hash"), current_value=state["source_hash"], recommended_action="re-export"))
            for name in editable:
                imported_value = self._coerce_value(name, row.get(name))
                row[name] = imported_value
                old_value = original[item_id].get(name)
                if imported_value != old_value:
                    changes.append(WebAssistDiff(item_id, name, old_value, imported_value, old_value))
            if state["package_type"] == "glossary_review":
                if (str(row.get("review_status", "")).lower() in {"approved", "resolved"}
                        and not str(row.get("user_final_translation", "")).strip()
                        and not self._truthy(row.get("preserve_original"))):
                    conflicts.append(WebAssistConflict(
                        "empty_final_value", item_id,
                        imported_value=row.get("user_final_translation"),
                        recommended_action="supply_value_or_preserve_original",
                    ))
                original_item = original.get(item_id, {})
                if (row.get("unit_source_sha256") != original_item.get("unit_source_sha256")
                        or row.get("unit_translation_sha256") != original_item.get("unit_translation_sha256")):
                    conflicts.append(WebAssistConflict(
                        "occurrence_source_changed", item_id,
                        recommended_action="re-export",
                    ))
            else:
                original_item = original.get(item_id, {})
                corrections = self._coerce_value("object_corrections", row.get("object_corrections"))
                free_text = str(row.get("user_corrected_markdown") or row.get("user_corrected_text") or "").strip()
                if free_text and not corrections:
                    conflicts.append(WebAssistConflict(
                        "unstructured_page_answer", item_id, imported_value=free_text,
                        recommended_action="complete_object_corrections",
                    ))
                known_objects = {
                    str(item.get("source_object_id")): item
                    for item in original_item.get("objects", [])
                    if item.get("source_object_id")
                }
                seen_objects: set[str] = set()
                for correction in corrections:
                    object_id = str(correction.get("source_object_id") or "")
                    if not object_id or object_id not in known_objects:
                        conflicts.append(WebAssistConflict(
                            "unknown_object", item_id, imported_value=object_id,
                            recommended_action="use_exported_object_id",
                        ))
                        continue
                    if object_id in seen_objects:
                        conflicts.append(WebAssistConflict(
                            "duplicate_object", item_id, imported_value=object_id,
                            recommended_action="deduplicate",
                        ))
                        continue
                    seen_objects.add(object_id)
                    unknown_fields = set(correction) - (
                        DIFFICULT_OBJECT_EDITABLE_FIELDS
                        | {"source_object_id", "translation_unit_id", "source_text_sha256",
                           "translated_text_sha256"}
                    )
                    if unknown_fields:
                        conflicts.append(WebAssistConflict(
                            "unsupported_object_field", item_id,
                            imported_value=sorted(unknown_fields), recommended_action="remove_fields",
                        ))
                    expected = known_objects[object_id]
                    if (str(correction.get("translation_unit_id") or "")
                            != str(expected.get("translation_unit_id") or "")):
                        conflicts.append(WebAssistConflict(
                            "object_unit_mismatch", item_id, imported_value=object_id,
                            recommended_action="re-export",
                        ))
                    if (str(correction.get("source_text_sha256") or "")
                            != str(expected.get("source_text_sha256") or "")):
                        conflicts.append(WebAssistConflict(
                            "object_source_changed", item_id, imported_value=object_id,
                            recommended_action="re-export",
                        ))
                    if (str(correction.get("corrected_source_text") or "").strip()
                            and not str(correction.get("corrected_translated_text") or "").strip()):
                        conflicts.append(WebAssistConflict(
                            "missing_corresponding_translation", item_id,
                            imported_value=object_id,
                            recommended_action="supply_object_translation",
                        ))
        missing = set(original) - seen
        for item_id in sorted(missing):
            conflicts.append(WebAssistConflict("missing_item", item_id, recommended_action="keep_current"))
        validation = WebAssistImportValidation(
            package_id=request.package_id,
            valid=not conflicts,
            import_sha256=_tree_sha256(import_path),
            changes=changes,
            conflicts=conflicts,
            imported_items=len(seen),
        )
        record = asdict(validation) | {"import_path": str(import_path), "created_at": _now(), "rows": rows}
        state["validations"].append(record)
        state["status"] = "validated" if validation.valid else "conflict"
        state["updated_at"] = _now()
        _write_json(self._state_path(request.package_id), state)
        self._audit(request.package_id, state["package_type"], "validate", import_sha256=validation.import_sha256, conflict_count=len(conflicts), failed_count=0 if validation.valid else 1)
        return validation

    def preview_diff(self, package_id: str) -> dict[str, Any]:
        state = self._load_state(package_id)
        if not state["validations"]:
            raise RuntimeError("validateWebAssistImport is required before preview")
        validation = state["validations"][-1]
        return {
            "package_id": package_id,
            "valid": validation["valid"],
            "changes": validation["changes"],
            "conflicts": validation["conflicts"],
            "summary": {"changes": len(validation["changes"]), "conflicts": len(validation["conflicts"])},
        }

    def apply_import(self, package_id: str) -> WebAssistApplyResult:
        state = self._load_state(package_id)
        if not state["validations"]:
            raise RuntimeError("validated import is required")
        validation = state["validations"][-1]
        if not validation["valid"] or validation["conflicts"]:
            raise RuntimeError("web-assist import has unresolved conflicts")
        application_id = _id("webassist_apply")
        revision = self.revision_root / application_id
        revision.mkdir(parents=True, exist_ok=False)
        affected_outputs: list[str] = []
        rows = validation["rows"]
        id_field = "term_id" if state["package_type"] == "glossary_review" else "page_item_id"
        originals = {item[id_field]: item for item in state["items"]}
        workspace = self._workspace_for_state(state)
        units_path = workspace / "data/translation_units.jsonl"
        if not units_path.is_file():
            raise RuntimeError("generic workspace translation units are required for incremental rebuild")
        units = [json.loads(line) for line in units_path.read_text("utf-8").splitlines() if line.strip()]
        manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
        cache_dir = workspace / "cache" / manifest["language_pair"]
        overlay_path = workspace / "manual_review/imported_objects.json"
        if overlay_path.is_file():
            shutil.copy2(overlay_path, revision / "manual_review.before.json")
            overlay_payload = json.loads(overlay_path.read_text("utf-8"))
            overlay_records = overlay_payload.get("objects", overlay_payload)
        else:
            (revision / "manual_review.absent").write_text("absent\n", "utf-8")
            overlay_records = []
        overlays = {item["object_id"]: dict(item) for item in overlay_records
                    if isinstance(item, dict) and item.get("object_id")}
        invalidated_units: set[str] = set()
        if state["package_type"] == "glossary_review":
            edits_by_unit: dict[str, list[tuple[int, int, str, str, str]]] = {}
            for row in rows:
                item = originals.get(str(row.get(id_field, "")))
                if not item:
                    continue
                replacement = item["source_term"] if self._truthy(row.get("preserve_original")) else str(row.get("user_final_translation", "")).strip()
                current = str(item.get("current_translation") or "")
                start = item.get("translated_span_start")
                end = item.get("translated_span_end")
                if replacement and replacement != current:
                    if start is None or end is None:
                        raise RuntimeError("glossary occurrence has no verified translation span; re-export required")
                    edits_by_unit.setdefault(str(item["translation_unit_id"]), []).append(
                        (int(start), int(end), current, replacement,
                         str(item["unit_translation_sha256"]))
                    )
            for unit_id, edits in edits_by_unit.items():
                unit = next((item for item in units if item["translation_unit_id"] == unit_id), None)
                if unit is None:
                    raise RuntimeError("glossary translation unit is no longer available; re-export required")
                cache_path = cache_dir / f"{unit['translation_unit_id']}.json"
                if not cache_path.is_file():
                    raise RuntimeError("glossary translation cache is unavailable; re-export required")
                cached = json.loads(cache_path.read_text("utf-8"))
                translated = str(cached.get("translated_text") or "")
                expected_hashes = {item[4] for item in edits}
                if expected_hashes != {hashlib.sha256(translated.encode("utf-8")).hexdigest()}:
                    raise RuntimeError("glossary translation changed after export; re-export required")
                updated = translated
                for start, end, current, replacement, _ in sorted(edits, reverse=True):
                    if updated[start:end] != current:
                        raise RuntimeError("glossary occurrence span changed after export; re-export required")
                    updated = updated[:start] + replacement + updated[end:]
                if updated != translated:
                    record = overlays.get(unit["source_object_id"], {"object_id": unit["source_object_id"]})
                    record["translated_text"] = updated
                    record["provenance"] = {"source": "web_assist_glossary", "package_id": package_id}
                    overlays[unit["source_object_id"]] = record
                    invalidated_units.add(unit["translation_unit_id"])
            affected_roles = ["target", "bilingual"]
        else:
            for row in rows:
                item = originals.get(str(row.get(id_field, "")))
                if not item:
                    continue
                units_by_object = {str(unit["source_object_id"]): unit for unit in units}
                for correction in self._coerce_value("object_corrections", row.get("object_corrections")):
                    object_id = str(correction.get("source_object_id") or "")
                    unit = units_by_object.get(object_id)
                    if unit is None:
                        raise RuntimeError("difficult-page object is no longer available; re-export required")
                    record = overlays.get(unit["source_object_id"], {"object_id": unit["source_object_id"]})
                    corrected_source = str(correction.get("corrected_source_text") or "").strip()
                    corrected_translation = str(correction.get("corrected_translated_text") or "").strip()
                    if corrected_source:
                        record["source_text"] = corrected_source
                        record.pop("translated_text", None)
                    if corrected_translation:
                        record["translated_text"] = corrected_translation
                    record["provenance"] = {"source": "web_assist_difficult_page", "package_id": package_id,
                                            "physical_page": int(item["page_number"])}
                    overlays[unit["source_object_id"]] = record
                    if corrected_source and not corrected_translation:
                        cache_path = cache_dir / f"{unit['translation_unit_id']}.json"
                        if cache_path.is_file():
                            backup = revision / "cache" / cache_path.name
                            backup.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(cache_path, backup)
                            cache_path.unlink()
                    invalidated_units.add(unit["translation_unit_id"])
            affected_roles = ["source", "target", "bilingual"]
        _write_json(overlay_path, {"schema_version": "manual_review_objects.v1", "objects": list(overlays.values()),
                                  "web_assist_package_id": package_id})
        affected_outputs = [str(workspace / "rendered" / role) for role in affected_roles]
        applied_items = len({change["item_id"] for change in validation["changes"]})
        application = {
            "application_id": application_id,
            "package_id": package_id,
            "applied_at": _now(),
            "applied_items": applied_items,
            "affected_outputs": affected_outputs,
            "revision_path": str(revision),
            "workspace": str(workspace),
            "invalidated_units": sorted(invalidated_units),
            "affected_roles": affected_roles,
            "undone": False,
        }
        state["applications"].append(application)
        state["status"] = "applied"
        state["updated_at"] = _now()
        _write_json(self._state_path(package_id), state)
        self._audit(package_id, state["package_type"], "apply", item_count=applied_items, affected_outputs=affected_outputs)
        return WebAssistApplyResult(application_id, package_id, applied_items, 0, affected_outputs, True, True,
                                    sorted(invalidated_units), True)

    def undo_last_apply(self, project_id: str, source_document_id: str) -> dict[str, Any]:
        candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for path in self.state_root.glob("*.json"):
            state = json.loads(path.read_text("utf-8"))
            if (state["project_id"] != project_id
                    or state["source_document_id"] != source_document_id):
                continue
            for application in state.get("applications", []):
                if not application.get("undone"):
                    candidates.append((application["applied_at"], state, application))
        if not candidates:
            raise RuntimeError("no web-assist application is available to undo")
        _, state, application = max(candidates, key=lambda item: item[0])
        revision = Path(application["revision_path"])
        workspace = Path(application["workspace"])
        overlay_path = workspace / "manual_review/imported_objects.json"
        overlay_backup = revision / "manual_review.before.json"
        if overlay_backup.is_file():
            shutil.copy2(overlay_backup, overlay_path)
        elif (revision / "manual_review.absent").is_file() and overlay_path.is_file():
            overlay_path.unlink()
        manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
        cache_dir = workspace / "cache" / manifest["language_pair"]
        for backup in (revision / "cache").glob("*.json") if (revision / "cache").is_dir() else []:
            shutil.copy2(backup, cache_dir / backup.name)
        application["undone"] = True
        application["undone_at"] = _now()
        state["status"] = "undone"
        state["updated_at"] = _now()
        _write_json(self._state_path(state["package_id"]), state)
        self._audit(state["package_id"], state["package_type"], "undo", item_count=application["applied_items"], affected_outputs=application["affected_outputs"])
        return {"application_id": application["application_id"], "package_id": state["package_id"], "project_id": project_id,
                "source_document_id": state["source_document_id"], "undone": True,
                "restored_outputs": application["affected_outputs"], "rebuild_required": True}

    def discard_package(self, package_id: str) -> dict[str, Any]:
        state = self._load_state(package_id)
        state["status"] = "discarded"
        state["updated_at"] = _now()
        _write_json(self._state_path(package_id), state)
        self._audit(package_id, state["package_type"], "discard")
        return {"package_id": package_id, "discarded": True}

    def history(self, project_id: str | None = None) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for path in self.state_root.glob("*.json"):
            state = json.loads(path.read_text("utf-8"))
            if project_id and state["project_id"] != project_id:
                continue
            values.extend(state.get("applications", []))
        return sorted(values, key=lambda item: item["applied_at"])

    def _glossary_items(self, package_id: str, project_id: str, source: dict[str, Any], output_paths: list[str]) -> list[dict[str, Any]]:
        workspace = self._workspace_from_outputs(source["sha256"], output_paths)
        manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
        units = [
            json.loads(line) for line in (workspace / "data/translation_units.jsonl")
            .read_text("utf-8").splitlines() if line.strip()
        ]
        cache_dir = workspace / "cache" / manifest["language_pair"]
        latin_pattern = re.compile(
            r"\b(?:[A-Z][A-Za-z0-9’'\-]{2,}(?:\s+[A-Z][A-Za-z0-9’'\-]{2,}){0,3}|[A-Z]{2,12})\b"
        )
        cjk_pattern = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]{2,8}")
        excluded = {"The", "This", "That", "Chapter", "Page", "Markdown", "Bookflow", "Human", "Review"}
        candidates: list[dict[str, Any]] = []
        for unit in units:
            if unit.get("element_type") in {"pagination", "visual", "visual_text"}:
                continue
            cache = cache_dir / f"{unit['translation_unit_id']}.json"
            if not cache.is_file():
                continue
            translated = str(json.loads(cache.read_text("utf-8")).get("translated_text") or "")
            source_text = str(unit.get("source_text") or "")
            explicit: list[tuple[str, str | None, str]] = []
            for key in ("uncertain_terms", "terminology", "glossary_candidates"):
                values = unit.get(key) or []
                if isinstance(values, dict):
                    values = [values]
                for value in values if isinstance(values, list) else []:
                    if isinstance(value, dict):
                        term = str(value.get("source_term") or value.get("term") or "").strip()
                        current = str(value.get("current_translation") or "").strip() or None
                    else:
                        term = str(value).strip(); current = None
                    if term:
                        explicit.append((term, current, f"model_or_user_{key}"))
            matches = list(cjk_pattern.finditer(source_text)) if manifest["source_language"] in {"zh-Hans", "ja"} else list(latin_pattern.finditer(source_text))
            automatic = [(term, None, "basic_multilingual_candidate")
                         for term in dict.fromkeys(match.group(0) for match in matches)
                         if term not in excluded]
            unique_candidates = {
                (term, expected_translation, reason): None
                for term, expected_translation, reason in explicit + automatic
            }
            for term, expected_translation, reason in unique_candidates:
                for occurrence_index, match in enumerate(re.finditer(re.escape(term), source_text), 1):
                    current = expected_translation or term
                    translated_matches = list(re.finditer(re.escape(current), translated, re.I))
                    translated_match = (
                        translated_matches[occurrence_index - 1]
                        if occurrence_index <= len(translated_matches) else None
                    )
                    candidates.append({
                        "unit": unit, "source_text": source_text, "translated": translated,
                        "term": term, "source_start": match.start(), "source_end": match.end(),
                        "translated_start": translated_match.start() if translated_match else None,
                        "translated_end": translated_match.end() if translated_match else None,
                        "current_translation": translated_match.group(0) if translated_match else "",
                        "reason": reason, "occurrence_index": occurrence_index,
                    })
        counts: dict[str, int] = {}
        for item in candidates:
            normalized = re.sub(r"\s+", " ", item["term"]).casefold()
            counts[normalized] = counts.get(normalized, 0) + 1
        items: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates[:500], 1):
            unit = candidate["unit"]; source_text = candidate["source_text"]
            normalized = re.sub(r"\s+", " ", candidate["term"]).casefold()
            occurrence_fingerprint = (
                f"{unit['translation_unit_id']}|{candidate['source_start']}|"
                f"{candidate['source_end']}"
            )
            occurrence_id = "occ_" + hashlib.sha256(
                occurrence_fingerprint.encode("utf-8")
            ).hexdigest()[:20]
            item = GlossaryReviewItem(
                package_id=package_id,
                term_id=f"term_{index:04d}",
                project_id=project_id,
                source_document_id=source["source_id"],
                occurrence_id=occurrence_id,
                translation_unit_id=str(unit["translation_unit_id"]),
                source_object_id=str(unit["source_object_id"]),
                source_term=candidate["term"],
                normalized_term=normalized,
                detected_language=manifest["source_language"],
                page_number=int(unit.get("source_page") or 0) or None,
                segment_id=str(unit["translation_unit_id"]),
                context_before=source_text[max(0, candidate["source_start"] - 120):candidate["source_start"]].strip(),
                context_current=source_text[candidate["source_start"]:candidate["source_end"]],
                context_after=source_text[candidate["source_end"]:candidate["source_end"] + 120].strip(),
                current_translation=candidate["current_translation"],
                source_span_start=int(candidate["source_start"]),
                source_span_end=int(candidate["source_end"]),
                translated_span_start=candidate["translated_start"],
                translated_span_end=candidate["translated_end"],
                unit_source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                unit_translation_sha256=hashlib.sha256(candidate["translated"].encode("utf-8")).hexdigest(),
                candidate_translations=[candidate["current_translation"]] if candidate["current_translation"] else [],
                confidence=0.70 if candidate["translated_start"] is not None else 0.40,
                uncertainty_reason=candidate["reason"],
                occurrence_count=counts[normalized],
                suggested_action="confirm_translation" if candidate["translated_start"] is not None
                                 else "needs_span_alignment",
            )
            items.append(asdict(item))
        return items

    def _difficult_items(self, package_id: str, project_id: str, source: dict[str, Any], output_paths: list[str], export_path: Path) -> list[dict[str, Any]]:
        path = Path(source["source_path"])
        if path.suffix.lower() != ".pdf":
            issue = DifficultPageItem(
                package_id, "page_0001", project_id, source["source_id"], 1,
                "pages/page_0001.png", source["sha256"], "", "image source", "",
                ["image_only"], 0.4, "Image source requires visual review",
                "Create structured object corrections; free text is preview-only.",
            )
            (export_path / "pages").mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, export_path / "pages" / "page_0001.png")
            return [asdict(issue)]
        workspace = self._workspace_from_outputs(source["sha256"], output_paths)
        workspace_manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
        units = [
            json.loads(line) for line in (workspace / "data/translation_units.jsonl")
            .read_text("utf-8").splitlines() if line.strip()
        ]
        cache_dir = workspace / "cache" / workspace_manifest["language_pair"]
        units_by_page: dict[int, list[dict[str, Any]]] = {}
        for unit in units:
            units_by_page.setdefault(int(unit.get("source_page") or 0), []).append(unit)
        review_pages = {
            int(value) for value in workspace_manifest.get("review_pages", [])
            if str(value).isdigit()
        }
        issue_codes: dict[int, set[str]] = {}
        for filename in (
            "page_text_quality.jsonl", "ocr_routes.jsonl", "page_classification.jsonl",
            "page_quality.jsonl", "page_routes.jsonl",
        ):
            record_path = workspace / "data" / filename
            if not record_path.is_file():
                continue
            for record in (
                json.loads(line) for line in record_path.read_text("utf-8").splitlines()
                if line.strip()
            ):
                raw_page = record.get("physical_page") or record.get("page_number")
                if raw_page is None:
                    page_match = re.fullmatch(r"page-(\d+)", str(record.get("page_id") or ""))
                    raw_page = page_match.group(1) if page_match else 0
                page_no = int(raw_page or 0)
                if page_no < 1:
                    continue
                encoded = json.dumps(record, ensure_ascii=False).casefold()
                flags = record.get("issue_codes") or record.get("issues") or record.get("uncertainties") or []
                if isinstance(flags, str):
                    flags = [flags]
                if (any(token in encoded for token in ("review_required", "review_pending",
                                                        "difficult_page", "low_confidence"))
                        or flags):
                    review_pages.add(page_no)
                    issue_codes.setdefault(page_no, set()).update(str(value) for value in flags)
        document = fitz.open(path)
        items: list[dict[str, Any]] = []
        try:
            for index, page in enumerate(document):
                page_number = index + 1
                if page_number not in review_pages:
                    continue
                text = page.get_text("text").strip()
                item_id = f"page_{page_number:04d}"
                image_rel = f"pages/{item_id}.png"
                image_path = export_path / image_rel
                image_path.parent.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(image_path)
                object_rows: list[dict[str, Any]] = []
                for unit in sorted(units_by_page.get(page_number, []),
                                   key=lambda value: tuple(value.get("reading_order") or ())):
                    cache_path = cache_dir / f"{unit['translation_unit_id']}.json"
                    translated = ""
                    if cache_path.is_file():
                        translated = str(json.loads(cache_path.read_text("utf-8")).get("translated_text") or "")
                    source_text = str(unit.get("source_text") or "")
                    object_rows.append({
                        "source_object_id": unit["source_object_id"],
                        "translation_unit_id": unit["translation_unit_id"],
                        "element_type": unit.get("element_type"),
                        "bbox": unit.get("bbox"),
                        "source_text": source_text,
                        "translated_text": translated,
                        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                        "translated_text_sha256": hashlib.sha256(translated.encode("utf-8")).hexdigest(),
                    })
                current_markdown = "\n\n".join(
                    str(item["source_text"]) for item in object_rows if item["source_text"]
                )
                issues = sorted(issue_codes.get(page_number) or {"production_review_required"})
                item = DifficultPageItem(
                    package_id=package_id, page_item_id=item_id, project_id=project_id,
                    source_document_id=source["source_id"], page_number=page_number,
                    page_image=image_rel, source_hash=source["sha256"],
                    current_ocr=text,
                    current_structure=json.dumps([
                        {"source_object_id": value["source_object_id"],
                         "element_type": value["element_type"], "bbox": value["bbox"]}
                        for value in object_rows
                    ], ensure_ascii=False),
                    current_markdown=current_markdown,
                    issue_codes=issues, confidence=0.55 if object_rows else 0.30,
                    model_notes="Production quality records marked this page for review.",
                    suggested_task="Correct individual objects without changing page identity.",
                    objects=object_rows,
                )
                items.append(asdict(item))
        finally:
            document.close()
        return items

    def _export_glossary(
        self, path: Path, package_id: str, project_id: str,
        source: dict[str, Any], items: list[dict[str, Any]], *,
        source_language: str, target_language: str,
    ) -> None:
        columns = list(GlossaryReviewItem.__dataclass_fields__)
        payload = {"schema_version": "bookflow-web-assist-glossary-v1.2", "package_id": package_id, "package_type": "glossary_review", "project_id": project_id, "source_document_id": source["source_id"], "source_hash": source["sha256"], "items": items}
        _write_json(path / "glossary_review.json", payload)
        self._write_csv(path / "glossary_review.csv", items, columns)
        _write_xlsx(path / "glossary_review.xlsx", items, columns)
        prompt_id, prompt = _localized_official_prompt("glossary_review", target_language)
        (path / "OFFICIAL_PROMPT.md").write_text(prompt, "utf-8")
        instructions = _localized_human_instructions("glossary_review", target_language)
        (path / "README_FOR_HUMAN.md").write_text(instructions, "utf-8")
        (path / "README_FOR_WEB_AI.md").write_text(prompt, "utf-8")
        self._write_package_manifest(
            path, package_id, "glossary_review", project_id, source["source_id"],
            source["sha256"], prompt_id=prompt_id,
            source_language=source_language, target_language=target_language,
        )

    def _export_difficult(
        self, path: Path, package_id: str, project_id: str,
        source: dict[str, Any], items: list[dict[str, Any]], *,
        source_language: str, target_language: str,
    ) -> None:
        columns = list(DifficultPageItem.__dataclass_fields__)
        payload = {"schema_version": "bookflow-web-assist-difficult-pages-v1.2", "package_id": package_id, "package_type": "difficult_pages", "project_id": project_id, "source_document_id": source["source_id"], "source_hash": source["sha256"], "items": items}
        _write_json(path / "difficult_pages_index.json", payload)
        self._write_csv(path / "difficult_pages_index.csv", items, columns)
        _write_xlsx(path / "difficult_pages_index.xlsx", items, columns)
        for item in items:
            base = path / "pages" / item["page_item_id"]
            Path(f"{base}.current.txt").write_text(item["current_ocr"] + "\n", "utf-8")
            Path(f"{base}.current.md").write_text(item["current_markdown"] + "\n", "utf-8")
            _write_json(Path(f"{base}.task.json"), {key: item[key] for key in ("package_id", "page_item_id", "page_number", "source_hash", "issue_codes", "model_notes", "suggested_task")})
            corrections = [
                {
                    "source_object_id": value["source_object_id"],
                    "translation_unit_id": value["translation_unit_id"],
                    "source_text_sha256": value["source_text_sha256"],
                    "translated_text_sha256": value["translated_text_sha256"],
                    "corrected_source_text": "",
                    "corrected_translated_text": "",
                    "structure_note": "",
                    "review_status": "pending",
                }
                for value in item.get("objects", [])
            ]
            Path(f"{base}.answer.md").write_text(
                "# Structured object corrections\n\n"
                "<!-- Edit values only. Keep every ID and hash unchanged. -->\n\n"
                "```json\n" + json.dumps(corrections, ensure_ascii=False, indent=2)
                + "\n```\n",
                "utf-8",
            )
        prompt_id, prompt = _localized_official_prompt("difficult_pages", target_language)
        (path / "OFFICIAL_PROMPT.md").write_text(prompt, "utf-8")
        instructions = _localized_human_instructions("difficult_pages", target_language)
        (path / "README_FOR_HUMAN.md").write_text(instructions, "utf-8")
        (path / "README_FOR_WEB_AI.md").write_text(prompt, "utf-8")
        self._write_package_manifest(
            path, package_id, "difficult_pages", project_id, source["source_id"],
            source["sha256"], prompt_id=prompt_id,
            source_language=source_language, target_language=target_language,
        )

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})

    @staticmethod
    def _write_package_manifest(path: Path, package_id: str, package_type: str,
                                project_id: str, source_document_id: str,
                                source_hash: str, *, prompt_id: str,
                                source_language: str, target_language: str) -> None:
        files = []
        for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file() and candidate.name != "PACKAGE_MANIFEST.json"):
            files.append({"relative_path": item.relative_to(path).as_posix(), "size_bytes": item.stat().st_size, "sha256": _sha256(item)})
        prompt_path = path / "OFFICIAL_PROMPT.md"
        _write_json(path / "PACKAGE_MANIFEST.json", {
            "schema_version": "bookflow-web-assist-package-v1.3",
            "package_id": package_id, "package_type": package_type,
            "project_id": project_id, "source_document_id": source_document_id,
            "source_hash": source_hash, "prompt_id": prompt_id,
            "source_language": source_language, "target_language": target_language,
            "prompt_sha256": _sha256(prompt_path), "created_at": _now(), "files": files,
        })

    def _load_import_rows(self, path: Path, package_type: str) -> Any:
        if path.is_dir():
            package_path = path
            name = "glossary_review.json" if package_type == "glossary_review" else "difficult_pages_index.json"
            path = path / name
            payload = json.loads(path.read_text("utf-8-sig"))
            if package_type == "difficult_pages":
                rows = payload.get("items", payload) if isinstance(payload, dict) else payload
                for row in rows:
                    page_id = str(row.get("page_item_id", ""))
                    answer = package_path / "pages" / f"{page_id}.answer.md"
                    legacy_answer = package_path / f"{page_id}.answer.md"
                    if answer.is_file() and legacy_answer.is_file():
                        if answer.read_bytes() != legacy_answer.read_bytes():
                            raise ValueError(
                                f"conflicting new and legacy difficult-page answers: {page_id}"
                            )
                    selected_answer = answer if answer.is_file() else legacy_answer
                    if not selected_answer.is_file():
                        continue
                    correction = selected_answer.read_text("utf-8-sig").strip()
                    json_block = re.search(r"```json\s*(.*?)\s*```", correction, re.S | re.I)
                    if json_block:
                        parsed = json.loads(json_block.group(1))
                        if not isinstance(parsed, list):
                            raise ValueError("difficult-page object corrections must be a JSON array")
                        row["object_corrections"] = parsed
                    else:
                        visible = re.sub(r"<!--.*?-->", "", correction, flags=re.DOTALL).strip()
                        if visible and visible not in {"# Corrected page",
                                                       "# Structured object corrections"}:
                            row["user_corrected_markdown"] = correction
            return payload
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text("utf-8-sig"))
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        if path.suffix.lower() == ".xlsx":
            return _read_xlsx(path)
        raise ValueError("web-assist import must be JSON, CSV, XLSX, or an exported package directory")

    def _validate_import_surface(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        selected_file = path.is_file()
        candidates = [path] if selected_file else [item for item in path.rglob("*") if item.is_file()]
        for item in candidates:
            suffix = item.suffix.lower()
            if suffix == ".zip":
                if selected_file:
                    raise ValueError("extract the returned ZIP before importing it into Bookflow")
                continue
            if suffix in FORBIDDEN_IMPORT_SUFFIXES or suffix not in ALLOWED_IMPORT_SUFFIXES | {".png", ".jpg", ".jpeg"}:
                raise ValueError(f"unsupported or executable web-assist import file: {item.name}")
            if item.stat().st_size > 100 * 1024 * 1024:
                raise ValueError(f"web-assist import file exceeds size limit: {item.name}")

    @staticmethod
    def _coerce_value(name: str, value: Any) -> Any:
        if name == "preserve_original":
            return WebAssistService._truthy(value)
        if name == "object_corrections":
            if value in (None, ""):
                return []
            if isinstance(value, list):
                parsed = value
            if isinstance(value, str):
                parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError("object_corrections must be a JSON array")
            return [
                item for item in parsed if isinstance(item, dict) and (
                    str(item.get("corrected_source_text") or "").strip()
                    or str(item.get("corrected_translated_text") or "").strip()
                    or str(item.get("structure_note") or "").strip()
                    or str(item.get("review_status") or "pending").strip().casefold()
                    not in {"", "pending"}
                )
            ]
        return "" if value is None else str(value)

    @staticmethod
    def _truthy(value: Any) -> bool:
        return value is True or str(value).strip().casefold() in {"1", "true", "yes", "y"}

    def _load_state(self, package_id: str) -> dict[str, Any]:
        path = self._state_path(package_id)
        if not path.is_file():
            raise KeyError(f"web-assist package not found: {package_id}")
        return json.loads(path.read_text("utf-8"))

    def _state_path(self, package_id: str) -> Path:
        if not re.fullmatch(r"webassist_[a-z_]+_[0-9a-f]{32}", package_id):
            raise ValueError("invalid web-assist package_id")
        return self.state_root / f"{package_id}.json"

    def _audit(self, package_id: str, package_type: str, action: str, *, import_sha256: str | None = None, item_count: int = 0, conflict_count: int = 0, failed_count: int = 0, affected_outputs: list[str] | None = None) -> None:
        record = {
            "timestamp": _now(), "package_id": package_id, "package_type": package_type,
            "actor": "bookflow_backend", "action": action, "import_sha256": import_sha256,
            "applied_item_count": item_count if action == "apply" else 0,
            "item_count": item_count, "conflict_count": conflict_count, "failed_count": failed_count,
            "affected_documents": [], "affected_outputs": affected_outputs or [],
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
