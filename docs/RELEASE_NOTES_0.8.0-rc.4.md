# Bookflow Scholar 0.8.0-rc.4

Windows release-candidate hotfix for damaged OCR pages, pipeline blocking, and visible processing progress.

## Fixed

- Rejected PDF text layers and failed OCR/VLM pages remain available as review evidence but are excluded from translation-provider dispatch.
- Healthy pages continue through translation and reconstruction when another page needs manual review.
- Optional VLM structure enrichment now falls back to deterministic layout structure and marks affected pages for review instead of failing the whole book.
- OCR reports page-by-page progress, and translation reports each completed batch/translation-unit count to the desktop.
- Overall progress includes the active job rather than counting only fully completed books.
- The overview displays the real production stage, current page/unit count, overall percentage, and review-queue count in six languages.
- Failure envelopes identify the actual OCR, structure, translation, or output-build stage.

## Existing Windows dependency behavior

- Both distributions bootstrap Microsoft Edge WebView2 when it is missing.
- The persistent Python sidecar and native PDF renderer are bundled.
- LibreOffice remains optional and is not redistributed. Install it from the [official LibreOffice download page](https://www.libreoffice.org/download/) only for optional Office/DOCX compatibility rendering.

## Downloads

- `Bookflow-Scholar-0.8.0-rc.4-setup.exe`: current-user installer.
- `Bookflow-Scholar-0.8.0-rc.4-portable-win-x64.zip`: extract the complete ZIP, then start `Bookflow Scholar.exe`.
- `SHA256SUMS.txt`, runtime prerequisites, sidecar manifest, SBOM, and license inventory accompany the binaries.

This candidate is unsigned. Windows may display SmartScreen. Verify SHA-256 before running either distribution.
