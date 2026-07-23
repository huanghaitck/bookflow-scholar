"""Generate the six tiny, copyright-free, offline B2 fixtures."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent
TEXTS = {
    "zh-Hans": ("测试文档", "这是一个自行生成的测试页面。它包含中文标点、数字 2026 和短段落。\n第二段用于验证 UTF-8 文本是否保持完整。\n• 第一项\n• 第二项", "测试文档"),
    "en": ("Test Document", "This synthetic page checks English punctuation, number 2026, and text extraction.\nA second paragraph verifies stable UTF-8 output.\n• First item\n• Second item", "Test Document"),
    "fr": ("Document de test", "Cette page synthétique vérifie la ponctuation française, le nombre 2026 et l’extraction.\nUn deuxième paragraphe contrôle la sortie UTF-8.\n• Premier élément\n• Deuxième élément", "Document de test"),
    "de": ("Testdokument", "Diese synthetische Seite prüft deutsche Zeichensetzung, die Zahl 2026 und die Extraktion.\nEin zweiter Absatz prüft die UTF-8-Ausgabe.\n• Erster Punkt\n• Zweiter Punkt", "Testdokument"),
    "ja": ("テスト文書", "これは自動生成されたテストページです。日本語の句読点、数字 2026、本文を確認します。\n二つ目の段落で UTF-8 出力を確認します。\n・項目一\n・項目二", "テスト文書"),
    "es": ("Documento de prueba", "Esta página sintética comprueba la puntuación, el número 2026 y la extracción de texto.\nUn segundo párrafo verifica la salida UTF-8.\n• Primer elemento\n• Segundo elemento", "Documento de prueba"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    rows = []
    for language, (title, body, excerpt) in TEXTS.items():
        path = ROOT / f"{language}_synthetic_test_page.pdf"
        document = fitz.open(); page = document.new_page(width=595, height=842)
        font = "japan" if language == "ja" else "china-s" if language == "zh-Hans" else "helv"
        page.insert_text((56, 72), title, fontname=font, fontsize=18)
        page.insert_textbox(fitz.Rect(56, 105, 535, 760), body + "\n\n1", fontname=font, fontsize=11, lineheight=1.4)
        document.set_metadata({"title": title, "subject": "synthetic_test_fixture; not_product_content", "producer": "Bookflow B2 fixture generator"})
        document.save(path); document.close()
        rows.append({"language": language, "source_url": f"synthetic://bookflow-b2/{language}", "publisher": "Bookflow QA", "title": title, "license": "CC0-1.0 / self-generated", "retrieved_at": "2026-07-21", "mime_type": "application/pdf", "size_bytes": path.stat().st_size, "sha256": sha256(path), "local_path": path.name, "expected_text_excerpt": excerpt, "fixture_class": "synthetic_test_fixture", "product_content": "false"})
    fields = list(rows[0])
    with (ROOT / "SOURCE_MANIFEST.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


if __name__ == "__main__":
    main()
