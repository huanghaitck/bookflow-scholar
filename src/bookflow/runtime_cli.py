"""Installed, language-neutral Bookflow command line interface."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console

from . import __version__
from .credential_store import CredentialStore
from .manual_review import export_review_package, import_patch, load_records, validate_patch
from .multilingual_workspace import (
    OUTPUT_ROLES, build_workspace, control_workspace, create_workspace, inspect_workspace, plan_workspace,
    render_workspace, status_workspace, translate_workspace, validate_workspace,
    update_workspace_metadata,
)
from .provider_registry import ProviderRegistry, RegistryTranslationProvider
from .publication_structure import mechanical_preflight, run_structure_workspace


app = typer.Typer(name="bookflow", help="Configurable multilingual book production.", no_args_is_help=True)
workspace_app = typer.Typer(help="Create and manage isolated book workspaces.")
providers_app = typer.Typer(help="List, validate, and test configured providers.")
review_app = typer.Typer(help="Manual and web-assisted review workflow.")
renderers_app = typer.Typer(help="Discover and validate document renderer backends.")
credentials_app = typer.Typer(help="Store API keys in the system credential manager.")
app.add_typer(workspace_app, name="workspace")
app.add_typer(providers_app, name="providers")
app.add_typer(review_app, name="review")
app.add_typer(renderers_app, name="renderers")
app.add_typer(credentials_app, name="credentials")
console = Console()


def _emit(value: Any) -> None:
    console.print(json.dumps(value, ensure_ascii=False, indent=2))


def _provider_config(path: Path | None) -> Path:
    selected = path or (Path(os.environ["BOOKFLOW_PROVIDER_CONFIG"]) if os.getenv("BOOKFLOW_PROVIDER_CONFIG") else None)
    if selected is None:
        raise typer.BadParameter("--provider-config or BOOKFLOW_PROVIDER_CONFIG is required")
    if not selected.is_file():
        raise typer.BadParameter(f"provider config not found: {selected}")
    return selected


@app.command()
def version() -> None:
    """Show the installed Bookflow version."""
    _emit({"version": __version__, "import_path": str(Path(__file__).resolve())})


@workspace_app.command("create")
def workspace_create(
    pdf: Annotated[Path, typer.Option("--pdf")], workspace: Annotated[Path, typer.Option("--workspace")],
    source_language: Annotated[str, typer.Option("--source-language")] = "auto",
    target_language: Annotated[str, typer.Option("--target-language")] = "zh-Hans",
    output_directory: Annotated[Path | None, typer.Option("--output-directory")] = None,
    profile: Annotated[str, typer.Option("--profile")] = "reading",
    layout_mode: Annotated[str, typer.Option("--layout-mode")] = "text",
    bilingual_layout: Annotated[str, typer.Option("--bilingual-layout")] = "stacked",
    output_role: Annotated[str, typer.Option("--output-role")] = "all",
    title: Annotated[str | None, typer.Option("--title")] = None,
    author: Annotated[str | None, typer.Option("--author")] = None,
) -> None:
    """Create a new isolated workspace from any supported-language PDF."""
    _emit(create_workspace(workspace, pdf, source_language, target_language,
                           output_directory=output_directory, profile=profile,
                           layout_mode=layout_mode, bilingual_layout=bilingual_layout,
                           output_role=output_role, metadata={key: value for key, value in
                               {"book_title": title, "author": author}.items() if value}))


@workspace_app.command("metadata")
def workspace_metadata(
    workspace: Annotated[Path, typer.Option("--workspace")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    author: Annotated[str | None, typer.Option("--author")] = None,
    volume: Annotated[str | None, typer.Option("--volume")] = None,
    year: Annotated[str | None, typer.Option("--year")] = None,
    source_institution: Annotated[str | None, typer.Option("--source-institution")] = None,
    rights_notice: Annotated[str | None, typer.Option("--rights-notice")] = None,
) -> None:
    values = {"book_title": title, "author": author, "volume": volume, "year": year,
              "source_institution": source_institution, "rights_notice": rights_notice}
    _emit(update_workspace_metadata(workspace, {key: value for key, value in values.items() if value is not None}))


@app.command()
def inspect(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Inspect the complete source PDF and create stable translation units."""
    _emit(inspect_workspace(workspace))


@app.command()
def structure(
    workspace: Annotated[Path, typer.Option("--workspace")],
    provider_config: Annotated[Path | None, typer.Option("--provider-config")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    credentials_profile: Annotated[Path | None, typer.Option("--credentials-profile")] = None,
    max_batches: Annotated[int | None, typer.Option("--max-batches")] = None,
) -> None:
    """Run durable whole-book VLM structure analysis."""
    registry = ProviderRegistry.load(_provider_config(provider_config), credentials_profile=credentials_profile)
    mechanical_preflight(workspace)
    _emit(run_structure_workspace(workspace, registry, provider_id=provider, model=model, max_batches=max_batches))


@app.command()
def plan(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Show pending and cached translation work without model calls."""
    _emit(plan_workspace(workspace))


def _translate(workspace: Path, provider_config: Path | None, provider: str | None, model: str | None,
               credentials_profile: Path | None, max_units: int | None, batch_size: int, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return plan_workspace(workspace)
    registry = ProviderRegistry.load(_provider_config(provider_config), credentials_profile=credentials_profile)
    profile = registry.get(provider, "text")
    if model:
        from dataclasses import replace
        profile = replace(profile, model=model)
    if profile.provider_type == "mock":
        from .providers.mock import MockTranslationProvider
        adapter: Any = MockTranslationProvider()
    else:
        adapter = RegistryTranslationProvider(workspace, registry.client(profile))
    return translate_workspace(workspace, adapter, provider_name=profile.provider_id,
                               model=profile.model, batch_size=batch_size, max_units=max_units)


@app.command()
def translate(
    workspace: Annotated[Path, typer.Option("--workspace")],
    provider_config: Annotated[Path | None, typer.Option("--provider-config")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    credentials_profile: Annotated[Path | None, typer.Option("--credentials-profile")] = None,
    max_units: Annotated[int | None, typer.Option("--max-units")] = None,
    batch_size: Annotated[int, typer.Option("--batch-size")] = 8,
    dry_run: Annotated[bool, typer.Option("--dry-run/--execute")] = True,
) -> None:
    """Translate pending units through the configured text provider."""
    _emit(_translate(workspace, provider_config, provider, model, credentials_profile, max_units, batch_size, dry_run))


@app.command()
def resume(
    workspace: Annotated[Path, typer.Option("--workspace")],
    provider_config: Annotated[Path | None, typer.Option("--provider-config")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    credentials_profile: Annotated[Path | None, typer.Option("--credentials-profile")] = None,
    max_units: Annotated[int | None, typer.Option("--max-units")] = None,
    batch_size: Annotated[int, typer.Option("--batch-size")] = 8,
) -> None:
    """Resume pending work; completed units remain cache hits."""
    _emit(_translate(workspace, provider_config, provider, model, credentials_profile, max_units, batch_size, False))


@app.command()
def status(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Show real stages, generated paths, review counts, and next actions."""
    base = status_workspace(workspace); manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
    outputs = {}
    for role in OUTPUT_ROLES:
        path = Path(manifest["output_directory"]) / role / "render_manifest.json"
        outputs[role] = json.loads(path.read_text("utf-8"))["outputs"] if path.is_file() else "not_generated"
    structure_path = workspace / "data/book_structure.json"
    base.update({"workspace": str(workspace.resolve()), "outputs": outputs,
        "book_structure": str(structure_path) if structure_path.is_file() else "not_generated",
        "page_classification": str(workspace / "data/page_classification.jsonl") if (workspace / "data/page_classification.jsonl").is_file() else "not_generated",
        "segmentation_plan": str(workspace / "data/segmentation_plan.json") if (workspace / "data/segmentation_plan.json").is_file() else "not_generated",
        "publication_reconstruction": str(workspace / "data/publication_reconstruction.json") if (workspace / "data/publication_reconstruction.json").is_file() else "not_generated",
        "validation_report": str(workspace / "data/validation_report.json") if (workspace / "data/validation_report.json").is_file() else "not_generated",
        "review_pending": manifest.get("review_pending", 0),
        "next_action": "translate" if base["pending"] else "structure" if not structure_path.is_file() else "build"})
    _emit(base)


@app.command()
def render(
    workspace: Annotated[Path, typer.Option("--workspace")],
    role: Annotated[str, typer.Option("--role")] = "source",
    formats: Annotated[str, typer.Option("--formats")] = "md,docx,pdf",
    layout_mode: Annotated[str | None, typer.Option("--layout-mode")] = None,
    bilingual_layout: Annotated[str | None, typer.Option("--bilingual-layout")] = None,
    pdf_renderer: Annotated[str, typer.Option("--pdf-renderer")] = "native_pdf",
    renderer_config: Annotated[Path | None, typer.Option("--renderer-config")] = None,
) -> None:
    """Render one output role."""
    _emit(render_workspace(workspace, role, tuple(x.strip() for x in formats.split(",") if x.strip()),
                           layout_mode=layout_mode, bilingual_layout=bilingual_layout,
                           pdf_renderer=pdf_renderer, renderer_config=renderer_config))


@app.command()
def validate(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Validate translation completeness, outputs, and reading reflow."""
    result = validate_workspace(workspace); _emit(result)
    if not result["valid"]: raise typer.Exit(1)


@app.command()
def build(
    workspace: Annotated[Path, typer.Option("--workspace")],
    formats: Annotated[str, typer.Option("--formats")] = "md,docx,pdf",
    layout_mode: Annotated[str | None, typer.Option("--layout-mode")] = None,
    bilingual_layout: Annotated[str | None, typer.Option("--bilingual-layout")] = None,
    pdf_renderer: Annotated[str, typer.Option("--pdf-renderer")] = "native_pdf",
    renderer_config: Annotated[Path | None, typer.Option("--renderer-config")] = None,
) -> None:
    """Build source, target, and bilingual outputs."""
    _emit(build_workspace(workspace, tuple(x.strip() for x in formats.split(",") if x.strip()),
                          layout_mode=layout_mode, bilingual_layout=bilingual_layout,
                          pdf_renderer=pdf_renderer, renderer_config=renderer_config))


@app.command()
def rebuild(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    """Rebuild all outputs after an imported review patch."""
    _emit(build_workspace(workspace))


@app.command()
def pause(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    _emit(control_workspace(workspace, "pause"))


@app.command()
def cancel(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    _emit(control_workspace(workspace, "cancel"))


@credentials_app.command("list")
def credentials_list() -> None:
    _emit(CredentialStore().list())


@credentials_app.command("set")
def credentials_set(
    alias: Annotated[str, typer.Option("--alias")],
    secret: Annotated[str, typer.Option("--secret", prompt=True, hide_input=True)],
    process_only: Annotated[bool, typer.Option("--process-only")] = False,
) -> None:
    _emit(CredentialStore().set(alias, secret, process_only=process_only))


@credentials_app.command("delete")
def credentials_delete(alias: Annotated[str, typer.Option("--alias")]) -> None:
    _emit(CredentialStore().delete(alias))


@credentials_app.command("test")
def credentials_test(alias: Annotated[str, typer.Option("--alias")]) -> None:
    result = CredentialStore().test(alias); _emit(result)
    if not result["present"]: raise typer.Exit(1)


@credentials_app.command("migrate-env")
def credentials_migrate_env(
    alias: Annotated[str, typer.Option("--alias")],
    env_name: Annotated[str, typer.Option("--env")],
) -> None:
    value = os.getenv(env_name)
    if not value: raise typer.BadParameter(f"environment variable is missing: {env_name}")
    _emit(CredentialStore().set(alias, value))


@providers_app.command("list")
def providers_list(config: Annotated[Path | None, typer.Option("--config")] = None) -> None:
    registry = ProviderRegistry.load(_provider_config(config)); _emit(registry.validate()["providers"])


@providers_app.command("validate")
def providers_validate(config: Annotated[Path | None, typer.Option("--config")] = None) -> None:
    result = ProviderRegistry.load(_provider_config(config)).validate(); _emit(result)
    if not result["valid"]: raise typer.Exit(1)


@providers_app.command("test")
def providers_test(
    config: Annotated[Path | None, typer.Option("--config")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    credentials_profile: Annotated[Path | None, typer.Option("--credentials-profile")] = None,
) -> None:
    """Run a content-free provider handshake."""
    registry = ProviderRegistry.load(_provider_config(config), credentials_profile=credentials_profile)
    profile = registry.get(provider, "text"); raw = registry.client(profile).text_json(
        system_prompt="Return JSON only.", payload={"instruction": "Return exactly READY", "workspace_content": False})
    _emit({"provider_id": profile.provider_id, "model": profile.model, "ok": bool(raw.get("choices"))})


@renderers_app.command("list")
def renderers_list(config: Annotated[Path | None, typer.Option("--config")] = None) -> None:
    from .renderer_backend import load_renderer_config, renderer_list
    _emit(renderer_list(load_renderer_config(config)))


@renderers_app.command("detect")
def renderers_detect(config: Annotated[Path | None, typer.Option("--config")] = None) -> None:
    from .renderer_backend import detect_renderer, load_renderer_config
    _emit(detect_renderer(load_renderer_config(config)))


@renderers_app.command("validate")
def renderers_validate(config: Annotated[Path | None, typer.Option("--config")] = None) -> None:
    from .renderer_backend import load_renderer_config, test_renderer
    result = test_renderer(load_renderer_config(config)); _emit(result)
    if result.get("test_conversion") != "passed": raise typer.Exit(1)


@renderers_app.command("test")
def renderers_test(config: Annotated[Path | None, typer.Option("--config")] = None) -> None:
    from .renderer_backend import load_renderer_config, test_renderer
    result = test_renderer(load_renderer_config(config)); _emit(result)
    if result.get("test_conversion") != "passed": raise typer.Exit(1)


@app.command()
def doctor(renderer_config: Annotated[Path | None, typer.Option("--renderer-config")] = None) -> None:
    """Check runtime dependencies without model or book-content calls."""
    import platform
    from .renderer_backend import detect_renderer, load_renderer_config
    renderer = detect_renderer(load_renderer_config(renderer_config))
    _emit({"python": platform.python_version(), "bookflow": __version__, "office_renderer": renderer,
           "native_pdf_renderer": "available"})


@review_app.command("status")
def review_status(workspace: Annotated[Path, typer.Option("--workspace")]) -> None:
    path = workspace / "data/structure_review_objects.json"
    records = load_records(path) if path.is_file() else []
    _emit({"pending": sum(x.get("review_status") == "pending" for x in records), "objects": records})


@review_app.command("export")
def review_export(workspace: Annotated[Path, typer.Option("--workspace")],
                  output: Annotated[Path, typer.Option("--output")]) -> None:
    objects = workspace / "data/structure_review_objects.json"
    manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
    _emit(export_review_package(objects, output, source_pages_pdf=Path(manifest["source_pdf"]), complete=True))


@review_app.command("validate")
def review_validate(workspace: Annotated[Path, typer.Option("--workspace")],
                    patch: Annotated[Path, typer.Option("--patch")]) -> None:
    result = validate_patch(patch, workspace / "data/structure_review_objects.json"); _emit(result)
    if not result["valid"]: raise typer.Exit(1)


@review_app.command("import")
def review_import(workspace: Annotated[Path, typer.Option("--workspace")],
                  patch: Annotated[Path, typer.Option("--patch")],
                  dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True) -> None:
    _emit(import_patch(patch, workspace / "data/structure_review_objects.json",
        output_path=workspace / "manual_review/imported_objects.json",
        provenance_path=workspace / "manual_review/provenance.json", dry_run=dry_run))


@app.command("run")
def run_config(config: Annotated[Path, typer.Option("--config")]) -> None:
    """Execute a noninteractive lifecycle from a YAML configuration."""
    data = yaml.safe_load(config.read_text("utf-8")) or {}
    workspace = Path(data["workspace"])
    if not (workspace / "bookflow_workspace.json").is_file():
        create_workspace(workspace, Path(data["source_pdf"]), data.get("source_language", "auto"),
                         data["target_language"], output_directory=Path(data["output_directory"]) if data.get("output_directory") else None,
                         layout_mode=data.get("layout_mode", "text"), bilingual_layout=data.get("bilingual_layout", "stacked"),
                         output_role=data.get("output_role", "all"))
    inspect_workspace(workspace); plan_workspace(workspace)
    provider_config = Path(data["provider_config"])
    _translate(workspace, provider_config, data.get("text_provider"), data.get("text_model"),
               Path(data["credentials_profile"]) if data.get("credentials_profile") else None, None, 8, False)
    if data.get("publication_reconstruction"):
        registry = ProviderRegistry.load(provider_config, credentials_profile=Path(data["credentials_profile"]) if data.get("credentials_profile") else None)
        run_structure_workspace(workspace, registry, provider_id=data.get("vlm_provider"), model=data.get("vlm_model"))
    _emit(build_workspace(workspace, layout_mode=data.get("layout_mode"), bilingual_layout=data.get("bilingual_layout"),
                          pdf_renderer=data.get("pdf_renderer", "native_pdf"),
                          renderer_config=Path(data["renderer_config"]) if data.get("renderer_config") else None))


@app.command()
def ui(host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
       port: Annotated[int, typer.Option("--port")] = 8765,
       no_browser: Annotated[bool, typer.Option("--no-browser")] = False) -> None:
    """Open the local graphical interface backed by the same workspace services."""
    from .ui_server import serve
    serve(host=host, port=port, open_browser=not no_browser)


if __name__ == "__main__":
    app()
