"""Create single-page image-only fixtures from downloaded OHCHR documents."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent
ORIGINALS = ROOT / "originals"
RASTERIZED = ROOT / "rasterized"
URLS = {
    "zh-Hans": "https://www.ohchr.org/sites/default/files/UDHR/Documents/UDHR_Translations/chn.pdf",
    "en": "https://www.ohchr.org/en/UDHR/Documents/UDHR_Translations/eng.pdf",
    "fr": "https://www.ohchr.org/sites/default/files/UDHR/Documents/UDHR_Translations/frn.pdf",
    "de": "https://www.ohchr.org/en/UDHR/Documents/UDHR_Translations/ger.pdf",
    "ja": "https://www.ohchr.org/sites/default/files/UDHR/Documents/UDHR_Translations/jpn.pdf",
    "es": "https://www.ohchr.org/en/UDHR/Documents/UDHR_Translations/spn.pdf",
}
TITLES = {
    "zh-Hans": "世界人权宣言", "en": "Universal Declaration of Human Rights",
    "fr": "Déclaration universelle des droits de l'homme",
    "de": "Allgemeine Erklärung der Menschenrechte", "ja": "世界人権宣言",
    "es": "Declaración Universal de Derechos Humanos",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_excerpt(text: str) -> str:
    return " ".join(text.replace("\x00", " ").split())[:160]


def main() -> None:
    RASTERIZED.mkdir(parents=True, exist_ok=True)
    rows = []
    for language, url in URLS.items():
        original = ORIGINALS / f"{language}_udhr.pdf"
        document = fitz.open(original)
        candidates = [(index, document[index].get_text("text")) for index in range(min(4, len(document)))]
        page_index, source_text = max(candidates, key=lambda item: len(item[1].strip()))
        page = document[page_index]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2), alpha=False)
        png = RASTERIZED / f"{language}_udhr_page.png"; pixmap.save(png)
        document.close()
        rows.append({
            "language": language, "source_url": url, "publisher": "United Nations OHCHR",
            "title": TITLES[language], "license": "official publicly distributed UN/OHCHR document",
            "retrieved_at": "2026-07-21", "mime_type": "image/png",
            "size_bytes": png.stat().st_size, "sha256": digest(png),
            "local_path": str(png.relative_to(ROOT)).replace("\\", "/"),
            "page_number": page_index + 1, "expected_text_excerpt": clean_excerpt(source_text),
            "original_local_path": str(original.relative_to(ROOT)).replace("\\", "/"),
            "original_size_bytes": original.stat().st_size, "original_sha256": digest(original),
            "text_layer_characters": 0,
        })
    with (ROOT / "SOURCE_MANIFEST.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


if __name__ == "__main__":
    main()
