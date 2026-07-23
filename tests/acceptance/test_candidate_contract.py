from pathlib import Path
import zipfile


FORBIDDEN = (
    "book1_appendix_original.pdf",
    "book1_bilingual_appendix_rebuilt.docx",
    "data/fullbook",
    "references/phase12_5/book1",
)


def test_candidate_wheel_contains_no_book1_or_manual_workspace(candidate_wheel: Path) -> None:
    with zipfile.ZipFile(candidate_wheel) as archive:
        names = [name.replace("\\", "/").lower() for name in archive.namelist()]
    assert not any(token.lower() in name for name in names for token in FORBIDDEN)
