# Bookflow Scholar 0.8.0-rc.5

Follow-up release candidate completing the difficult-page round trip for pages with no accepted OCR text.

## Fixed

- Every excluded bad page now receives a stable review-only object ID, translation-unit ID, page identity, and hashes without entering normal provider translation batches.
- Difficult-page ZIP exports always contain an object-level `pages/page_xxxx.answer.md` template for these pages instead of an empty correction array.
- Review-only objects require both corrected source text and corrected translation before they can be marked resolved.
- Import validates package, Source, object IDs, translation-unit IDs, and hashes; whole-page free text remains preview-only.
- Applying valid corrections updates the object overlay, rebuilds source/target/bilingual outputs, and clears the resolved page from the review count.
- Undo restores both object corrections and the previous review-page state.
- Six-language official prompts and the desktop Web Assist page explain the review-only rule.

rc.5 includes the rc.4 fixes for OCR isolation, optional VLM structure fallback, actual-stage errors, and live page/unit progress.

This candidate is unsigned. Verify the release SHA-256 before running it.
