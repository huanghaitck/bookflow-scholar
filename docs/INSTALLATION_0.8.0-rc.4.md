# Bookflow Scholar 0.8.0-rc.4 installation

## Installer

1. Download `Bookflow-Scholar-0.8.0-rc.4-setup.exe` and `SHA256SUMS.txt` from the same GitHub release.
2. Verify the SHA-256 value, then run the current-user installer.
3. If WebView2 Runtime is absent, setup runs the included Microsoft-signed bootstrapper. Internet access is needed only for that one-time installation.

## Portable ZIP

1. Extract the entire ZIP to a normal writable directory; do not run it inside the archive.
2. Start `Bookflow Scholar.exe`, not the internal `bookflow-desktop.exe`.
3. The launcher checks WebView2 and starts the included Microsoft bootstrapper when required.

## Damaged or unreadable pages

Low-quality text layers are checked page by page. If OCR or visual recognition still cannot provide acceptable text, the page is excluded from translation, retained in the review evidence, and shown in the review queue. Other pages continue processing. Correct the page through the difficult-page review package and rebuild the outputs when needed.

## LibreOffice

The three standard PDF editions use the bundled native renderer and do not require LibreOffice. Bookflow does not redistribute it. Install [LibreOffice from its official project](https://www.libreoffice.org/download/) only for optional Office/DOCX compatibility rendering, then reopen Bookflow for automatic detection.

Projects and settings remain under `%LOCALAPPDATA%\Bookflow Scholar\`. Uninstall preserves user projects by default.
