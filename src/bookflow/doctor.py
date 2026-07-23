"""Offline environment diagnostics with secret-safe output."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel

from .paths import load_settings, project_root


DEPENDENCIES: dict[str, tuple[str, str]] = {
    "PyMuPDF": ("fitz", "PyMuPDF"),
    "Pillow": ("PIL", "Pillow"),
    "pydantic": ("pydantic", "pydantic"),
    "PyYAML": ("yaml", "PyYAML"),
    "python-dotenv": ("dotenv", "python-dotenv"),
    "openai": ("openai", "openai"),
    "zhipuai": ("zhipuai", "zhipuai"),
    "typer": ("typer", "typer"),
    "rich": ("rich", "rich"),
    "tenacity": ("tenacity", "tenacity"),
    "python-docx": ("docx", "python-docx"),
}

API_VARIABLES = ("ZAI_API_KEY", "DEEPSEEK_API_KEY")


class DependencyStatus(BaseModel):
    name: str
    available: bool
    version: str | None = None
    error: str | None = None


class DoctorReport(BaseModel):
    python_version: str
    python_executable: str
    conda_environment: str
    expected_conda_environment: str
    expected_environment_active: bool
    dependencies: list[DependencyStatus]
    project_root: str
    project_writable: bool
    input_directory_exists: bool
    output_directory_exists: bool
    configuration_exists: bool
    configuration_valid: bool
    api_environment_variables: dict[str, bool]
    offline: bool = True

    @property
    def ok(self) -> bool:
        return (
            sys.version_info[:2] == (3, 12)
            and self.expected_environment_active
            and all(item.available for item in self.dependencies)
            and self.project_writable
            and self.input_directory_exists
            and self.output_directory_exists
            and self.configuration_exists
            and self.configuration_valid
        )


def _check_project_writable(root: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".bookflow-doctor-", delete=True):
            return True
    except OSError:
        return False


def _dependency_status(name: str, import_name: str, distribution: str) -> DependencyStatus:
    try:
        importlib.import_module(import_name)
        version = importlib.metadata.version(distribution)
        return DependencyStatus(name=name, available=True, version=version)
    except Exception as exc:  # doctor must report rather than crash
        return DependencyStatus(
            name=name,
            available=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def run_doctor(
    *,
    root: Path | None = None,
    config_path: Path | None = None,
    expected_environment: str = "bilingual-book",
) -> DoctorReport:
    """Run local checks only. No network or API call is performed."""

    root = (root or project_root()).resolve()
    config_path = config_path or root / "config" / "settings.example.yaml"
    dependencies = [
        _dependency_status(name, import_name, distribution)
        for name, (import_name, distribution) in DEPENDENCIES.items()
    ]

    conda_environment = os.environ.get("CONDA_DEFAULT_ENV") or Path(sys.prefix).name
    configuration_exists = config_path.is_file()
    configuration_valid = False
    if configuration_exists:
        try:
            load_settings(config_path)
            configuration_valid = True
        except Exception:
            configuration_valid = False

    return DoctorReport(
        python_version=sys.version.split()[0],
        python_executable=sys.executable,
        conda_environment=conda_environment,
        expected_conda_environment=expected_environment,
        expected_environment_active=conda_environment == expected_environment,
        dependencies=dependencies,
        project_root=str(root),
        project_writable=_check_project_writable(root),
        input_directory_exists=(root / "input").is_dir(),
        output_directory_exists=(root / "output").is_dir(),
        configuration_exists=configuration_exists,
        configuration_valid=configuration_valid,
        api_environment_variables={
            variable: variable in os.environ for variable in API_VARIABLES
        },
    )


def format_doctor_report(report: DoctorReport) -> str:
    """Create secret-safe plain text suitable for terminals and tests."""

    lines = [
        f"Python: {report.python_version}",
        f"Interpreter: {report.python_executable}",
        (
            f"Conda environment: {report.conda_environment} "
            f"(expected {report.expected_conda_environment})"
        ),
        f"Project root: {report.project_root}",
        f"Project writable: {'yes' if report.project_writable else 'no'}",
        f"input directory: {'present' if report.input_directory_exists else 'missing'}",
        f"output directory: {'present' if report.output_directory_exists else 'missing'}",
        f"Configuration: {'valid' if report.configuration_valid else 'missing or invalid'}",
        "Dependencies:",
    ]
    for item in report.dependencies:
        status = f"available ({item.version})" if item.available else "missing/import failed"
        lines.append(f"  - {item.name}: {status}")
    lines.append("API environment variables (values are never displayed):")
    for variable, is_set in report.api_environment_variables.items():
        lines.append(f"  - {variable}: {'set' if is_set else 'not set'}")
    lines.append("Network/API calls: disabled")
    lines.append(f"Overall: {'PASS' if report.ok else 'FAIL'}")
    return "\n".join(lines)
