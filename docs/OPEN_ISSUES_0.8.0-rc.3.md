# Open issues for 0.8.0-rc.3

- The Bookflow executables and installer are not code-signed; Windows SmartScreen may warn about an unknown publisher.
- WebView2 bootstrap installation requires network access when WebView2 Runtime is missing. A fully offline fixed-runtime distribution is not included because it would substantially increase package size.
- LibreOffice is optional and separately installed. Detection confirms the local executable, but Office-format compatibility can still vary by LibreOffice build and document fonts.
- A clean external Windows machine without WebView2 is still required for final independent confirmation of the newly packaged bootstrap path.
