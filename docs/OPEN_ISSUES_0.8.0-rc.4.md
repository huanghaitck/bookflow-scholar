# Open issues for 0.8.0-rc.4

- The Bookflow executables and installer are unsigned; Windows SmartScreen may show an unknown-publisher warning.
- Installing WebView2 when absent requires network access.
- LibreOffice is an optional separate installation, and Office-format compatibility varies by build and installed fonts.
- The external tester's original 1863 scanned PDF was not provided in this workspace. Synthetic damaged-text and raster-scan fixtures pass, but the exact affected pages still require an external-machine retest.
- Real Provider execution was not possible on the build machine because API credentials had previously been removed. Credential preflight made zero network calls and the redacted attempt ledger remained empty.
