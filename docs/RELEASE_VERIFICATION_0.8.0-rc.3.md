# Release verification: 0.8.0-rc.3

Verified on Windows 11 with the release files generated from the approved Python 3.12 project environment.

## Published hashes

| File | SHA-256 |
|---|---|
| `Bookflow-Scholar-0.8.0-rc.3-setup.exe` | `d42b7c0e3d8cc49436a45cdc5f5f2db42de7ab22342a0f6ca41c73480a04b59a` |
| `Bookflow-Scholar-0.8.0-rc.3-portable-win-x64.zip` | `86e3ccec5f443ffb8478bdb1fa722c7a3955dd35f69cd83fab5e46d441779b71` |
| `sidecar-manifest.json` | `439e00782e7a69cb06284629b343c77d3f9e5f4c8a8e24bd7cb9554774cbd354` |
| `runtime-prerequisites.json` | `bdab8d814684fb7c8b25ddbcd7b05d8ed2df3ba5d61445b997369ea825d24312` |
| `sbom.cdx.json` | `a2227407cad70b6cea88214c98c6877ead7c43b4c492b3e4b2fb9ad29fd9dc02` |
| `THIRD_PARTY_LICENSES.md` | `16994d77dfa16df012fb3834182d1437324af425b2867de3f8d07c9e4c92791f` |

The authoritative complete list is the release asset `SHA256SUMS.txt`.

## Checks completed

- Environment gate passed with `C:\Users\huanghai\.conda\envs\bilingual-book\python.exe` (Python 3.12.13).
- Backend contract/batch target tests: 12 passed.
- TypeScript and Vite production build: passed.
- Python and PowerShell release-script syntax checks: passed.
- Microsoft WebView2 Evergreen Bootstrapper Authenticode: valid; signer `Microsoft Corporation`.
- Portable archive contains the launcher, internal desktop executable, persistent sidecar, default provider configuration, and WebView2 bootstrapper.
- Portable extract-and-launch smoke test: passed.
- Final current-user installer install, installed-directory launch, and silent uninstall: passed.
- Uninstall removed the program directory and preserved all 253 pre-existing user-data files without changes.
- Renderer snapshot: bundled DOCX/native-PDF renderers available; local LibreOffice 26.2.4.2 detected as optional; no executable path exposed to the UI snapshot.
- Final release scan found no source-tree path, user-profile path, `BOOKFLOW_PYTHON`, API-key pattern, or `.env` file. The only `.pem` file is certifi's public CA trust bundle.

## Remaining external confirmation

This machine already has WebView2 Runtime. Removing a shared system runtime would risk other applications, so the missing-runtime branch was not forced locally. The installer and portable launcher both check 64-bit and 32-bit registry views, embed the Microsoft-signed Evergreen Bootstrapper, and block startup if bootstrap installation fails. A clean external Windows machine without WebView2 remains the final independent confirmation recorded in `OPEN_ISSUES.md`.
