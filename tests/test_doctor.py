from __future__ import annotations

import socket
from pathlib import Path

import yaml

from bookflow.cli import version
from bookflow.doctor import format_doctor_report, run_doctor


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "source_pdf": "input/book (1913).pdf",
                "sample_pdf": "input/sample_10_pages.pdf",
                "output_directory": "output",
                "cache_directory": "cache",
                "vision_provider": "provider-a",
                "vision_base_url": "https://example.invalid/v1",
                "vision_model": "vision-a",
                "translation_provider": "provider-b",
                "translation_base_url": "https://example.invalid/v1",
                "translation_model": "translation-a",
                "maximum_cash_cost_cny": 2.0,
                "default_page_range": [1, 11],
                "dry_run": True,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return config_path


def _doctor_root(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    return tmp_path, _write_config(tmp_path)


def test_doctor_never_displays_api_secret_values(tmp_path, monkeypatch):
    root, config = _doctor_root(tmp_path)
    secret_a = "phase1a-super-secret-zai-value"
    secret_b = "phase1a-super-secret-deepseek-value"
    monkeypatch.setenv("ZAI_API_KEY", secret_a)
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_b)
    report = run_doctor(root=root, config_path=config)
    rendered = format_doctor_report(report)
    assert secret_a not in rendered
    assert secret_b not in rendered
    assert "ZAI_API_KEY: set" in rendered
    assert "DEEPSEEK_API_KEY: set" in rendered


def test_doctor_does_not_crash_when_api_variables_are_missing(tmp_path, monkeypatch):
    root, config = _doctor_root(tmp_path)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    report = run_doctor(root=root, config_path=config)
    rendered = format_doctor_report(report)
    assert "ZAI_API_KEY: not set" in rendered
    assert "DEEPSEEK_API_KEY: not set" in rendered


def test_doctor_and_version_do_not_use_network(tmp_path, monkeypatch):
    root, config = _doctor_root(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = run_doctor(root=root, config_path=config)
    assert report.offline is True
    version()

