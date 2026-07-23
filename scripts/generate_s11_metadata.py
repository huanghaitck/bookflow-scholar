"""Generate deterministic release hashes, sidecar manifest, SBOM, and license inventory."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement


DIRECT_PYTHON_DISTRIBUTIONS = {
    "PyMuPDF",
    "Pillow",
    "pydantic",
    "PyYAML",
    "python-dotenv",
    "openai",
    "zhipuai",
    "typer",
    "rich",
    "tenacity",
    "python-docx",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def python_closure() -> list[metadata.Distribution]:
    installed = {normalized(dist.metadata["Name"]): dist for dist in metadata.distributions()
                 if dist.metadata.get("Name")}
    pending = [normalized(name) for name in DIRECT_PYTHON_DISTRIBUTIONS]
    selected: dict[str, metadata.Distribution] = {}
    while pending:
        name = pending.pop()
        if name in selected or name not in installed:
            continue
        dist = installed[name]
        selected[name] = dist
        for requirement_text in dist.requires or ():
            try:
                requirement = Requirement(requirement_text)
            except ValueError:
                continue
            if requirement.marker and not requirement.marker.evaluate():
                continue
            pending.append(normalized(requirement.name))
    return sorted(selected.values(), key=lambda item: normalized(item.metadata["Name"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--sidecar-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--portable", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    sidecar_dir = args.sidecar_dir.resolve()
    installer = args.installer.resolve()
    portable = args.portable.resolve() if args.portable else None
    repo_root = args.repo_root.resolve()
    release_dir.mkdir(parents=True, exist_ok=True)

    sidecar_files = []
    for path in sorted((item for item in sidecar_dir.rglob("*") if item.is_file()),
                       key=lambda item: item.relative_to(sidecar_dir).as_posix()):
        sidecar_files.append({
            "path": path.relative_to(sidecar_dir).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        })
    manifest = {
        "schema_version": 1,
        "product": "Bookflow Scholar",
        "version": args.version,
        "packaging": "PyInstaller onedir",
        "entrypoint": "bookflow-sidecar.exe",
        "file_count": len(sidecar_files),
        "files": sidecar_files,
    }
    manifest_path = release_dir / "sidecar-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    python_components = []
    license_rows = []
    for dist in python_closure():
        name = dist.metadata["Name"]
        version = dist.version
        license_name = (dist.metadata.get("License-Expression") or dist.metadata.get("License")
                        or "NOASSERTION").strip().splitlines()[0]
        homepage = dist.metadata.get("Home-page") or dist.metadata.get("Project-URL") or ""
        python_components.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{normalized(name)}@{version}",
            "licenses": [{"license": {"name": license_name}}],
        })
        license_rows.append(("Python", name, version, license_name, homepage))

    cargo = tomllib.loads((repo_root / "ui/src-tauri/Cargo.lock").read_text("utf-8"))
    rust_components = []
    for package in cargo.get("package", []):
        name, version = package["name"], package["version"]
        rust_components.append({
            "type": "library", "name": name, "version": version,
            "purl": f"pkg:cargo/{name}@{version}",
        })
        license_rows.append(("Rust", name, version, "See crate metadata", "https://crates.io/crates/" + name))

    package_json = json.loads((repo_root / "ui/package.json").read_text("utf-8"))
    npm_components = []
    for section in ("dependencies", "devDependencies"):
        for name, version in sorted(package_json.get(section, {}).items()):
            clean_version = version.lstrip("^~")
            npm_components.append({
                "type": "library", "name": name, "version": clean_version,
                "scope": "required" if section == "dependencies" else "excluded",
                "purl": f"pkg:npm/{name.replace('@', '%40')}@{clean_version}",
            })
            package_meta = repo_root / "ui/node_modules" / name / "package.json"
            npm_license = "NOASSERTION"
            homepage = "https://www.npmjs.com/package/" + name
            if package_meta.is_file():
                value = json.loads(package_meta.read_text("utf-8"))
                npm_license = str(value.get("license") or "NOASSERTION")
                homepage = str(value.get("homepage") or homepage)
            license_rows.append(("Node", name, clean_version, npm_license, homepage))

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "Bookflow Scholar",
                "version": args.version,
            }
        },
        "components": python_components + rust_components + npm_components,
    }
    (release_dir / "sbom.cdx.json").write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    license_lines = [
        "# Third-party licenses",
        "",
        "Generated from the dependency metadata available in the approved build environment.",
        "Redistribution must follow the complete license texts shipped by each dependency.",
        "",
        "| Ecosystem | Component | Version | Declared license | Project |",
        "|---|---|---:|---|---|",
    ]
    for ecosystem, name, version, license_name, homepage in sorted(license_rows):
        license_lines.append(
            f"| {ecosystem} | {name.replace('|', '/')} | {version} | "
            f"{license_name.replace('|', '/')} | {homepage.replace('|', '%7C')} |")
    (release_dir / "THIRD_PARTY_LICENSES.md").write_text(
        "\n".join(license_lines) + "\n", encoding="utf-8")

    sidecar_tree_hash = hashlib.sha256(
        "".join(f"{item['sha256']}  {item['path']}\n" for item in sidecar_files).encode("utf-8")
    ).hexdigest()
    hashes = [
        f"{sha256(installer)}  {installer.name}",
        f"{sidecar_tree_hash}  bookflow-sidecar.tree",
        f"{sha256(manifest_path)}  {manifest_path.name}",
        f"{sha256(release_dir / 'sbom.cdx.json')}  sbom.cdx.json",
        f"{sha256(release_dir / 'THIRD_PARTY_LICENSES.md')}  THIRD_PARTY_LICENSES.md",
    ]
    if portable:
        if not portable.is_file():
            raise FileNotFoundError(f"Portable archive not found: {portable}")
        hashes.insert(1, f"{sha256(portable)}  {portable.name}")
    (release_dir / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
