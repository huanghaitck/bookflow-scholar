# Bookflow Scholar 0.8.0-rc.5 installation

Install `Bookflow-Scholar-0.8.0-rc.5-setup.exe`, or extract the complete portable ZIP and start `Bookflow Scholar.exe`. WebView2 is bootstrapped when missing. User projects remain under `%LOCALAPPDATA%\Bookflow Scholar\`.

For a bad page, open **Web-assisted review → Difficult pages**, export the ZIP, and use `OFFICIAL_PROMPT.md` with an image-capable multimodal model. In `pages/page_xxxx.answer.md`, retain all IDs and hashes. A review-only object with no accepted OCR requires both `corrected_source_text` and `corrected_translated_text`; then set `review_status` to `resolved`.

Back in Bookflow, select the returned package directory, then run **Validate import → Preview diff → Apply**. Whole-page free text is not applied automatically. A successful object-level application rebuilds all three editions and clears the resolved page from the review count.

LibreOffice remains optional and is available from its [official download page](https://www.libreoffice.org/download/).
