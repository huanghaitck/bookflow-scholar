# Open issues for 0.8.0-rc.5

- Executables remain unsigned and may trigger SmartScreen.
- The external tester's original 1863 PDF still requires retesting; synthetic damaged-text and raster-scan fixtures cannot prove exact historical-font OCR accuracy.
- Real Provider execution was unavailable on the build machine because API credentials had been removed; no network calls or usage were incurred.
- WebView2 bootstrap needs network access when the runtime is absent; LibreOffice remains an optional separate dependency.
