from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
PAGE = fitz.paper_rect("a4")
LATIN_FONT = Path(r"C:\Windows\Fonts\NotoSans-Regular.ttf")
SC_FONT = Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf")
JP_FONT = Path(r"C:\Windows\Fonts\NotoSansJP-VF.ttf")

CONTENT = {
    "zh-Hans": {
        "font": SC_FONT,
        "title": "跨页观测站研究",
        "p1": "研究对象为三座观测站：Aster、Birch 和 Cedar。2024 年 4 月 18 日，Birch 标记点向东移动了 12 m。图 1 用箭头连接移动前后的两个位置。",
        "p2": "Aster 的位置没有移动；Cedar 只更换了传感器。第 2 页表格中所有 Birch 数据都指向移动后的新位置，因此解释表格时必须结合本页正文、图像和图注。",
        "figure": "图 1　Birch 标记点向东移动 12 m；Aster 未移动，Cedar 仅更换传感器。",
        "east": "向东 12 m",
        "table_title": "观测站读数与位置状态",
        "headers": ["站点", "位置状态", "读数（°C）", "上午", "下午", "变化量", "备注"],
        "rows": [
            ["Aster", "未移动", "18.4", "18.1", "-0.3", "稳定"],
            ["Birch", "图 1 移动后", "21.6", "24.8", "+3.2†", "采用新位置"],
            ["Cedar", "更换传感器", "19.7", "20.1", "+0.4", "位置未移动"],
        ],
        "footnote": "† Birch 的 +3.2 仅适用于图 1 所示向东移动 12 m 后的新位置。",
        "after": "若忽略第 1 页图 1 的位置变化，Birch 行会被错误地归到旧位置；因此表格解释必须同时读取上一页正文、图像和图注。",
    },
    "en": {
        "font": LATIN_FONT,
        "title": "Cross-page Observatory Study",
        "p1": "The study examines three observatories: Aster, Birch, and Cedar. On 18 April 2024, the Birch marker moved 12 m east. Figure 1 links the old and new positions with an arrow.",
        "p2": "Aster did not move; Cedar only changed its sensor. Every Birch value in the table on page 2 refers to the new position, so the table must be interpreted with this page's text, figure, and caption.",
        "figure": "Figure 1. Birch moved 12 m east; Aster did not move, and Cedar only changed its sensor.",
        "east": "12 m east",
        "table_title": "Observatory readings and position status",
        "headers": ["Site", "Position status", "Readings (°C)", "Morning", "Afternoon", "Change", "Notes"],
        "rows": [
            ["Aster", "Not moved", "18.4", "18.1", "-0.3", "Stable"],
            ["Birch", "After Figure 1 move", "21.6", "24.8", "+3.2†", "New position"],
            ["Cedar", "Sensor changed", "19.7", "20.1", "+0.4", "Position unchanged"],
        ],
        "footnote": "† Birch's +3.2 applies only to the new position after the 12 m eastward move shown in Figure 1.",
        "after": "If the position change in Figure 1 on page 1 is ignored, the Birch row will be assigned to the old position. The table therefore requires the preceding text, figure, and caption.",
    },
    "fr": {
        "font": LATIN_FONT,
        "title": "Étude interpage des observatoires",
        "p1": "L'étude porte sur trois observatoires : Aster, Birch et Cedar. Le 18 avril 2024, le repère Birch s'est déplacé de 12 m vers l'est. La figure 1 relie l'ancienne et la nouvelle position par une flèche.",
        "p2": "Aster n'a pas bougé ; Cedar a seulement changé de capteur. Toutes les valeurs Birch du tableau de la page 2 désignent la nouvelle position ; il faut donc lire le tableau avec le texte, la figure et la légende de cette page.",
        "figure": "Figure 1. Birch s'est déplacé de 12 m vers l'est ; Aster n'a pas bougé et Cedar a seulement changé de capteur.",
        "east": "12 m vers l'est",
        "table_title": "Mesures et état de position des observatoires",
        "headers": ["Site", "État de position", "Mesures (°C)", "Matin", "Après-midi", "Variation", "Remarques"],
        "rows": [
            ["Aster", "Non déplacé", "18.4", "18.1", "-0.3", "Stable"],
            ["Birch", "Après déplacement fig. 1", "21.6", "24.8", "+3.2†", "Nouvelle position"],
            ["Cedar", "Capteur remplacé", "19.7", "20.1", "+0.4", "Position inchangée"],
        ],
        "footnote": "† Le +3.2 de Birch ne vaut que pour la nouvelle position après le déplacement de 12 m vers l'est montré à la figure 1.",
        "after": "Si le changement de position de la figure 1, page 1, est ignoré, la ligne Birch sera attribuée à l'ancienne position. Le tableau exige donc le texte, la figure et la légende précédents.",
    },
    "de": {
        "font": LATIN_FONT,
        "title": "Seitenübergreifende Observatoriumsstudie",
        "p1": "Untersucht werden drei Observatorien: Aster, Birch und Cedar. Am 18. April 2024 wurde die Birch-Markierung 12 m nach Osten versetzt. Abbildung 1 verbindet die alte und die neue Position mit einem Pfeil.",
        "p2": "Aster wurde nicht versetzt; bei Cedar wurde nur der Sensor ausgetauscht. Alle Birch-Werte in der Tabelle auf Seite 2 beziehen sich auf die neue Position. Die Tabelle muss daher zusammen mit Text, Abbildung und Bildunterschrift gelesen werden.",
        "figure": "Abbildung 1. Birch wurde 12 m nach Osten versetzt; Aster blieb stehen, Cedar erhielt nur einen neuen Sensor.",
        "east": "12 m nach Osten",
        "table_title": "Messwerte und Positionsstatus der Observatorien",
        "headers": ["Station", "Positionsstatus", "Messwerte (°C)", "Vormittag", "Nachmittag", "Änderung", "Hinweise"],
        "rows": [
            ["Aster", "Nicht versetzt", "18.4", "18.1", "-0.3", "Stabil"],
            ["Birch", "Nach Versetzung in Abb. 1", "21.6", "24.8", "+3.2†", "Neue Position"],
            ["Cedar", "Sensor ausgetauscht", "19.7", "20.1", "+0.4", "Position unverändert"],
        ],
        "footnote": "† Der Birch-Wert +3.2 gilt nur für die neue Position nach der in Abbildung 1 gezeigten Verschiebung um 12 m nach Osten.",
        "after": "Wird die Positionsänderung in Abbildung 1 auf Seite 1 ignoriert, wird die Birch-Zeile der alten Position zugeordnet. Deshalb sind der vorherige Text, die Abbildung und die Bildunterschrift erforderlich.",
    },
    "ja": {
        "font": JP_FONT,
        "title": "ページ横断観測所研究",
        "p1": "研究対象は Aster、Birch、Cedar の三つの観測所である。2024年4月18日、Birch の標識点は東へ 12 m 移動した。図1は移動前と移動後の位置を矢印で結んでいる。",
        "p2": "Aster は移動しておらず、Cedar はセンサーだけを交換した。2ページの表にある Birch の値はすべて新しい位置を指すため、表はこのページの本文・図・図注と合わせて解釈しなければならない。",
        "figure": "図1　Birch は東へ 12 m 移動した。Aster は移動せず、Cedar はセンサーだけを交換した。",
        "east": "東へ 12 m",
        "table_title": "観測所の測定値と位置状態",
        "headers": ["地点", "位置状態", "測定値（°C）", "午前", "午後", "変化量", "備考"],
        "rows": [
            ["Aster", "移動なし", "18.4", "18.1", "-0.3", "安定"],
            ["Birch", "図1の移動後", "21.6", "24.8", "+3.2†", "新位置を使用"],
            ["Cedar", "センサー交換", "19.7", "20.1", "+0.4", "位置は不変"],
        ],
        "footnote": "† Birch の +3.2 は、図1に示す東へ 12 m 移動した後の新しい位置にのみ適用される。",
        "after": "1ページの図1の位置変化を無視すると、Birch の行は旧位置に誤って割り当てられる。したがって、表の解釈には前ページの本文・図・図注が必要である。",
    },
    "es": {
        "font": LATIN_FONT,
        "title": "Estudio interpagina de observatorios",
        "p1": "El estudio examina tres observatorios: Aster, Birch y Cedar. El 18 de abril de 2024, el marcador Birch se desplazó 12 m hacia el este. La figura 1 une la posición anterior y la nueva con una flecha.",
        "p2": "Aster no se desplazó; Cedar solo cambió el sensor. Todos los valores de Birch en la tabla de la página 2 se refieren a la nueva posición, por lo que la tabla debe interpretarse con el texto, la figura y el pie de esta página.",
        "figure": "Figura 1. Birch se desplazó 12 m al este; Aster no se movió y Cedar solo cambió el sensor.",
        "east": "12 m al este",
        "table_title": "Lecturas y estado de posición de los observatorios",
        "headers": ["Sitio", "Estado de posición", "Lecturas (°C)", "Mañana", "Tarde", "Cambio", "Notas"],
        "rows": [
            ["Aster", "Sin mover", "18.4", "18.1", "-0.3", "Estable"],
            ["Birch", "Tras el movimiento de fig. 1", "21.6", "24.8", "+3.2†", "Nueva posición"],
            ["Cedar", "Sensor cambiado", "19.7", "20.1", "+0.4", "Posición sin cambio"],
        ],
        "footnote": "† El +3.2 de Birch solo corresponde a la nueva posición tras el desplazamiento de 12 m al este mostrado en la figura 1.",
        "after": "Si se ignora el cambio de posición de la figura 1 en la página 1, la fila Birch se asignará a la posición anterior. Por ello, la tabla requiere el texto, la figura y el pie precedentes.",
    },
}


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int, draw: ImageDraw.ImageDraw, *, cjk: bool) -> list[str]:
    tokens = list(text) if cjk else text.split()
    lines: list[str] = []
    line = ""
    for token in tokens:
        candidate = f"{line}{token}" if cjk or not line else f"{line} {token}"
        if line and draw.textlength(candidate, font=font) > width:
            lines.append(line)
            line = token
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def draw_wrapped(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.FreeTypeFont, *, cjk: bool, fill: str = "#172033", align: str = "left") -> None:
    x0, y0, x1, y1 = box
    lines = wrap(text, font, x1 - x0 - 16, draw, cjk=cjk)
    spacing = max(4, font.size // 4)
    line_height = font.size + spacing
    y = y0 + max(4, (y1 - y0 - line_height * len(lines)) // 2)
    for line in lines:
        width = draw.textlength(line, font=font)
        x = x0 + 8 if align == "left" else x0 + max(8, (x1 - x0 - width) / 2)
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height


def add_page_one(doc: fitz.Document, language: str, item: dict[str, object]) -> int:
    page = doc.new_page(width=PAGE.width, height=PAGE.height)
    font_path = str(item["font"])
    alias = f"fixture-{language}"
    page.insert_textbox(fitz.Rect(54, 38, 541, 78), str(item["title"]), fontsize=20, fontname=alias, fontfile=font_path, align=fitz.TEXT_ALIGN_CENTER, color=(0.08, 0.12, 0.2))
    page.insert_textbox(fitz.Rect(58, 92, 537, 170), str(item["p1"]), fontsize=11.5, lineheight=1.45, fontname=alias, fontfile=font_path, color=(0.1, 0.13, 0.18))
    page.insert_textbox(fitz.Rect(58, 178, 537, 264), str(item["p2"]), fontsize=11.5, lineheight=1.45, fontname=alias, fontfile=font_path, color=(0.1, 0.13, 0.18))

    frame = fitz.Rect(74, 292, 521, 606)
    page.draw_rect(frame, color=(0.27, 0.34, 0.47), fill=(0.96, 0.97, 0.99), width=1)
    points = {"Aster": (148, 382), "Birch-old": (252, 484), "Birch-new": (398, 484), "Cedar": (440, 358)}
    for label, (x, y) in points.items():
        page.draw_circle((x, y), 7, color=(0.09, 0.33, 0.54), fill=(0.26, 0.58, 0.78), width=1)
        page.insert_text((x - 30, y - 14), label, fontsize=9.5, fontname=alias, fontfile=font_path, color=(0.08, 0.12, 0.2))
    page.draw_line(points["Birch-old"], points["Birch-new"], color=(0.78, 0.2, 0.16), width=2.2)
    page.draw_polyline([(388, 477), (398, 484), (388, 491)], color=(0.78, 0.2, 0.16), width=2.2)
    page.insert_textbox(fitz.Rect(280, 440, 386, 470), str(item["east"]), fontsize=10, fontname=alias, fontfile=font_path, align=fitz.TEXT_ALIGN_CENTER, color=(0.63, 0.13, 0.12))
    page.draw_line((484, 404), (484, 327), color=(0.08, 0.12, 0.2), width=1.6)
    page.draw_polyline([(477, 338), (484, 327), (491, 338)], color=(0.08, 0.12, 0.2), width=1.6)
    page.insert_text((478, 320), "N", fontsize=11, fontname=alias, fontfile=font_path)
    page.insert_textbox(fitz.Rect(64, 624, 531, 682), str(item["figure"]), fontsize=10.5, lineheight=1.35, fontname=alias, fontfile=font_path, align=fitz.TEXT_ALIGN_CENTER, color=(0.12, 0.17, 0.24))
    page.insert_text((520, 807), "1", fontsize=9, fontname=alias, fontfile=font_path, color=(0.38, 0.42, 0.48))
    return len(page.get_text().strip())


def raster_page_two(language: str, item: dict[str, object]) -> bytes:
    image = Image.new("RGB", (1200, 1680), "white")
    draw = ImageDraw.Draw(image)
    font_path = str(item["font"])
    title_font = ImageFont.truetype(font_path, 38)
    head_font = ImageFont.truetype(font_path, 22)
    cell_font = ImageFont.truetype(font_path, 21)
    small_font = ImageFont.truetype(font_path, 19)
    cjk = language in {"zh-Hans", "ja"}
    draw_wrapped(draw, (70, 55, 1130, 135), str(item["table_title"]), title_font, cjk=cjk, align="center")

    left, top = 50, 180
    widths = [130, 260, 150, 150, 150, 260]
    heights = [78, 70, 160, 160, 160]
    xs = [left]
    for value in widths:
        xs.append(xs[-1] + value)
    ys = [top]
    for value in heights:
        ys.append(ys[-1] + value)
    draw.rectangle((xs[0], ys[0], xs[-1], ys[-1]), outline="#26364d", width=4)
    for x in xs[1:-1]:
        draw.line((x, ys[0], x, ys[-1]), fill="#52647a", width=2)
    for y in ys[1:-1]:
        draw.line((xs[0], y, xs[-1], y), fill="#52647a", width=2)
    draw.rectangle((xs[2], ys[0], xs[4], ys[1]), fill="#dcebf5", outline="#52647a", width=2)
    for col in [0, 1, 4, 5]:
        draw.rectangle((xs[col], ys[0], xs[col + 1], ys[2]), fill="#dcebf5", outline="#52647a", width=2)
    draw.rectangle((xs[2], ys[1], xs[3], ys[2]), fill="#eaf2f7", outline="#52647a", width=2)
    draw.rectangle((xs[3], ys[1], xs[4], ys[2]), fill="#eaf2f7", outline="#52647a", width=2)
    headers = item["headers"]
    draw_wrapped(draw, (xs[0], ys[0], xs[1], ys[2]), headers[0], head_font, cjk=cjk, align="center")
    draw_wrapped(draw, (xs[1], ys[0], xs[2], ys[2]), headers[1], head_font, cjk=cjk, align="center")
    draw_wrapped(draw, (xs[2], ys[0], xs[4], ys[1]), headers[2], head_font, cjk=cjk, align="center")
    draw_wrapped(draw, (xs[2], ys[1], xs[3], ys[2]), headers[3], head_font, cjk=cjk, align="center")
    draw_wrapped(draw, (xs[3], ys[1], xs[4], ys[2]), headers[4], head_font, cjk=cjk, align="center")
    draw_wrapped(draw, (xs[4], ys[0], xs[5], ys[2]), headers[5], head_font, cjk=cjk, align="center")
    draw_wrapped(draw, (xs[5], ys[0], xs[6], ys[2]), headers[6], head_font, cjk=cjk, align="center")
    for row_index, row in enumerate(item["rows"], start=2):
        for col, value in enumerate(row):
            draw_wrapped(draw, (xs[col], ys[row_index], xs[col + 1], ys[row_index + 1]), value, cell_font, cjk=cjk, align="center")

    draw_wrapped(draw, (70, 1040, 1130, 1145), str(item["footnote"]), small_font, cjk=cjk)
    draw.line((70, 1180, 1130, 1180), fill="#a6afba", width=2)
    draw_wrapped(draw, (70, 1210, 1130, 1440), str(item["after"]), ImageFont.truetype(font_path, 24), cjk=cjk)
    draw.text((1080, 1600), "2", font=small_font, fill="#657184")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_fixture(language: str, item: dict[str, object]) -> tuple[Path, int]:
    output = ROOT / f"fixture_{language}.pdf"
    doc = fitz.open()
    page_one_chars = add_page_one(doc, language, item)
    page_two = doc.new_page(width=PAGE.width, height=PAGE.height)
    page_two.insert_image(page_two.rect, stream=raster_page_two(language, item))
    doc.set_metadata({"title": str(item["title"]), "subject": "Bookflow two-page multilingual audit fixture", "keywords": f"Bookflow fixture {language}"})
    doc.save(output, garbage=4, deflate=True)
    doc.close()
    return output, page_one_chars


def main() -> None:
    for path in (LATIN_FONT, SC_FONT, JP_FONT):
        if not path.is_file():
            raise FileNotFoundError(path)
    manifest_rows = []
    localized = {}
    for language, item in CONTENT.items():
        output, page_one_chars = build_fixture(language, item)
        check = fitz.open(output)
        page_text = [page.get_text().strip() for page in check]
        if check.page_count != 2 or not page_text[0] or page_text[1]:
            raise RuntimeError(f"invalid fixture contract for {language}: pages={check.page_count}, text={[len(v) for v in page_text]}")
        check.close()
        manifest_rows.append({
            "language": language,
            "filename": output.name,
            "pages": 2,
            "page1_text_chars": page_one_chars,
            "page2_text_chars": 0,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        })
        localized[language] = {key: value for key, value in item.items() if key != "font"}

    with (ROOT / "fixture_manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)
    (ROOT / "fixture_source_content.json").write_text(json.dumps(localized, ensure_ascii=False, indent=2), encoding="utf-8")
    gold = {
        "schema_version": "bookflow-six-language-two-page-fixture-v1",
        "canonical_semantic_id": "observatory-position-change-v1",
        "authoritative_languages": list(CONTENT),
        "entities": ["Aster", "Birch-old", "Birch-new", "Cedar"],
        "dates": ["2024-04-18"],
        "measurements": [{"value": 12, "unit": "m", "direction": "east"}, {"site": "Birch-new", "value": 3.2, "sign": "+"}],
        "units": ["m", "°C"],
        "figure_nodes": ["Aster", "Birch-old", "Birch-new", "Cedar"],
        "figure_edges": [{"from": "Birch-old", "to": "Birch-new", "relation": "12 m east"}],
        "table_headers": ["site", "position_status", "morning", "afternoon", "change", "notes"],
        "table_cells": {"Aster": [18.4, 18.1, -0.3], "Birch-new": [21.6, 24.8, 3.2], "Cedar": [19.7, 20.1, 0.4]},
        "footnotes": [{"marker": "†", "applies_to": "Birch-new", "value": "+3.2"}],
        "cross_page_relations": [
            "Birch-old != Birch-new",
            "Birch-new is 12 m east of Birch-old",
            "table.Birch refers to Birch-new",
            "Aster did not move",
            "Cedar changed sensor, not location",
            "+3.2 belongs to Birch-new",
        ],
        "expected_language": {language: language for language in CONTENT},
        "localized_text": localized,
    }
    (ROOT / "fixture_gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Six-language two-page fixture generation report",
        "",
        "Generated entirely from local text and vector/raster drawing code; no external or copyrighted image assets were used.",
        "",
        "- Page 1: born-digital text plus a self-drawn position diagram and caption.",
        "- Page 2: a two-level table, footnote, and cross-page explanation rendered as one raster image with no PDF text layer.",
        "- Formal language set: zh-Hans, en, fr, de, ja, es, sourced from the production multilingual workspace contract.",
        "- Every file has exactly two pages; page 1 has extractable text and page 2 has zero extractable characters.",
        "",
        "See `fixture_manifest.csv` for hashes and validation counts and `fixture_gold.json` for canonical semantics.",
    ]
    (ROOT / "fixture_generation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
