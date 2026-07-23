from __future__ import annotations

from pathlib import Path

import yaml

from bookflow.paths import load_settings, resolve_project_path


def _settings(vision_model: str, translation_model: str) -> dict[str, object]:
    return {
        "source_pdf": "input/The big game (1913).pdf",
        "sample_pdf": "input/sample_10_pages.pdf",
        "output_directory": "output",
        "cache_directory": "cache",
        "vision_provider": "configurable-vision",
        "vision_base_url": "https://vision.example.invalid/v1",
        "vision_model": vision_model,
        "translation_provider": "configurable-translation",
        "translation_base_url": "https://translation.example.invalid/v1",
        "translation_model": translation_model,
        "maximum_cash_cost_cny": 2.0,
        "default_page_range": [1, 11],
        "dry_run": True,
    }


def test_path_with_spaces_and_parentheses_is_preserved(tmp_path):
    relative = Path("input") / "The big game (1913).pdf"
    resolved = resolve_project_path(relative, root=tmp_path)
    assert resolved == (tmp_path / relative).resolve()


def test_models_can_change_in_configuration_without_code_change(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text(
        yaml.safe_dump(_settings("vision-model-one", "translation-model-one")),
        encoding="utf-8",
    )
    first = load_settings(config)
    config.write_text(
        yaml.safe_dump(_settings("vision-model-two", "translation-model-two")),
        encoding="utf-8",
    )
    second = load_settings(config)
    assert first.vision_model == "vision-model-one"
    assert second.vision_model == "vision-model-two"
    assert first.translation_model == "translation-model-one"
    assert second.translation_model == "translation-model-two"

