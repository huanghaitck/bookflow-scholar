# Bookflow Scholar 0.8.0-rc.3 installation

## Installer

1. Download `Bookflow-Scholar-0.8.0-rc.3-setup.exe` and `SHA256SUMS.txt` from the same GitHub release.
2. Verify the SHA-256 value.
3. Run the installer. It installs for the current Windows user and does not require Python, Conda, Node.js, Rust, or the source repository.
4. If Microsoft Edge WebView2 Runtime is absent, setup runs the signed Microsoft bootstrapper included in the package. Keep the computer online for this one-time installation.

## Portable ZIP

1. Extract the entire ZIP to a normal writable directory. Do not run it inside the archive and do not move individual files out of the extracted folder.
2. Start `Bookflow Scholar.exe`. This launcher checks WebView2 and installs it through the included signed Microsoft bootstrapper when necessary.
3. Do not start `bookflow-desktop.exe` directly; it is the internal application executable.

## LibreOffice

LibreOffice is optional and is not included in either distribution. The three standard PDF editions use Bookflow's bundled native PDF renderer. Bookflow does not mirror or redistribute LibreOffice. Install it only if you want the optional Office/DOCX compatibility rendering path, and obtain it from the [official LibreOffice download page](https://www.libreoffice.org/download/). Reopen Bookflow after installation; the overview displays whether a usable local LibreOffice installation was detected.

## User data and security

- Projects and settings are stored under `%LOCALAPPDATA%\Bookflow Scholar\`.
- API keys are stored through Windows Credential Manager and are not included in the release files.
- Uninstalling the application preserves user projects by default.
- This release is unsigned, so Windows may show SmartScreen. Verify the published SHA-256 before continuing.
