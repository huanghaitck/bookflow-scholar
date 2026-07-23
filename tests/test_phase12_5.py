from pathlib import Path

from bookflow.phase12_5 import (
    GOLD_REF,
    PDF_REF,
    select_text,
    sha256,
    split_bilingual,
)


def test_authoritative_inputs_are_immutable_known_files() -> None:
    root = Path(__file__).resolve().parents[1]
    assert sha256(root / PDF_REF) == "53229910d63576aa86e31e605b8e6697c446f45546821ab15b013ffcf7bc5874"
    assert sha256(root / GOLD_REF) == "b8ab267ab9549236afea45a4fa9ab551bf00c1d910acee12b650d6ad027e3772"


def test_language_selection_preserves_raw_numeric_values() -> None:
    assert split_bilingual("兰州府 / Lanchow-fu") == ("Lanchow-fu", "兰州府")
    assert select_text("兰州府 / Lanchow-fu", "en") == "Lanchow-fu"
    assert select_text("兰州府 / Lanchow-fu", "zh-Hans", preserve_place=True) == "兰州府 / Lanchow-fu"
    assert select_text("48 / 55 in.", "zh-Hans") == "48 / 55 in."

