# Vision transcription prompt v2

Transcribe and classify exactly one old English printed book page. Return one JSON object only. Do not translate, summarize, modernize, silently correct, or add text that is outside the visible image.

Preserve visible spelling, capitalization, punctuation, names, paragraph order, and historical wording. Separate body, chapter_title, section_title, footnote, caption, header, footer, page_number, illustration, and unknown content. A running header is not a chapter title merely because it is uppercase. Never mix a header, footer, page number, or scanning watermark into body text.

Boundary rules:

- `continuation_from_previous` and `continuation_to_next` must each be `true`, `false`, or `null`.
- Use `true` only when the current image itself clearly shows continuation.
- Use `false` only when the current image itself clearly shows a complete boundary.
- Use `null` when one isolated page is insufficient; never default missing evidence to false.
- Do not supply unseen words from adjacent pages.
- Explain visible evidence in `boundary_notes`.

Exact field types:

- `printed_page`: JSON string or null. Examples: `"6"`, `"vi"`, `"Plate 6"`, `null`. Never return a JSON number.
- `uncertain_characters`: JSON array of strings, always. Use `[]` when none are uncertain.
- `blocks`: JSON array. Every block has `block_id`, `block_type`, `order`, `text`, `bounding_box`, `confidence`, `uncertain`, and `notes`.
- Use null rather than invented coordinates or confidence.
- `warnings`: JSON array of strings.
- `translation_ready`: always false.
- `status`: `technical_validation_only` or `needs_review`.

Required top-level fields: schema_version, document_id, pdf_page, provider, model, page_type, printed_page, title, running_header, footer, page_number_text, blocks, continuation_from_previous, continuation_to_next, boundary_notes, uncertain_characters, warnings, status, translation_ready.

Do not include unknown fields, Markdown fences, Chinese text, cross-page merge text, or commentary.
