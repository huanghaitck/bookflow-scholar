# Bookflow Scholar 0.8.0-rc.3

Windows dependency hotfix for the installer and portable release.

## Fixed

- The installer now embeds Microsoft's signed WebView2 Evergreen Bootstrapper and runs it when the current user has no WebView2 Runtime.
- The portable ZIP now includes the same signed bootstrapper and a launcher that checks WebView2 before starting the desktop application.
- The overview reports the bundled DOCX/native-PDF renderers and the separately detected, optional LibreOffice capability instead of always reporting LibreOffice as unavailable.
- Runtime prerequisites, hashes, SBOM entries, license inventory, and portable instructions now describe the actual packaged behavior.

## Dependency boundaries

- **Required:** Microsoft Edge WebView2 Runtime. If missing, setup or the portable launcher starts the included Microsoft bootstrapper; an internet connection is required for that first installation.
- **Bundled:** the persistent Python sidecar and the native PDF renderer. Users do not need Python, Conda, Node.js, Rust, or a source checkout.
- **Optional and not bundled:** LibreOffice. It is used only for optional Office/DOCX compatibility rendering. Source, target-language, and bilingual PDFs do not require it.

## Downloads

- `Bookflow-Scholar-0.8.0-rc.3-setup.exe`: current-user installer.
- `Bookflow-Scholar-0.8.0-rc.3-portable-win-x64.zip`: extract the complete ZIP, then start `Bookflow Scholar.exe`.
- `SHA256SUMS.txt`: integrity hashes.
- `runtime-prerequisites.json`, `sidecar-manifest.json`, `sbom.cdx.json`, and `THIRD_PARTY_LICENSES.md`: release inventories.

This candidate is unsigned. Windows may display SmartScreen. Verify SHA-256 before running either distribution.
