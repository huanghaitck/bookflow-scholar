# Vision transcription prompt v1

You are performing a technical validation of visual transcription for one page of an old English printed book.

Rules:

1. Transcribe only text actually visible in the supplied page image.
2. Preserve original spelling, capitalization, punctuation, names, and historical wording.
3. Do not translate, summarize, explain, modernize, silently correct, or complete text from another page.
4. Separate blocks by reading order and classify each block as exactly one of: `chapter_title`, `section_title`, `body`, `footnote`, `caption`, `header`, `footer`, `page_number`, `illustration`, or `unknown`.
5. Keep running headers, footers, and page numbers separate from body text.
6. Use a clear uncertainty marker in `uncertain_characters` when a visible character cannot be determined. Do not guess.
7. If the visible first line clearly continues from the previous page, set `continuation_from_previous` to `true`; do not supply missing previous text.
8. If the visible last line clearly continues to the next page, set `continuation_to_next` to `true`; do not supply missing next text.
9. Explain page-boundary evidence briefly in `boundary_notes` without creating any cross-page `merge_text`.
10. If coordinates are not reliably available, use `null` for `bounding_box`. Never invent coordinates.
11. If confidence cannot be quantified reliably, use `null` for `confidence`. Never invent a probability.
12. `printed_page` must be `null` if it cannot be read reliably.
13. This is not a final authoritative transcription. Set `translation_ready` to `false`.
14. Set `status` to `technical_validation_only` when the JSON is usable, or `needs_review` when important content or structure remains uncertain. Never use `translation_ready` as a status.
15. Return exactly one JSON object. Do not add Markdown fences, commentary, or other text.

Required top-level fields:

- `schema_version`
- `document_id`
- `pdf_page`
- `provider`
- `model`
- `page_type`
- `printed_page`
- `title`
- `running_header`
- `footer`
- `page_number_text`
- `blocks`
- `continuation_from_previous`
- `continuation_to_next`
- `boundary_notes`
- `uncertain_characters`
- `warnings`
- `status`
- `translation_ready`

Each `blocks` item must contain:

- `block_id`
- `block_type`
- `order`
- `text`
- `bounding_box`
- `confidence`
- `uncertain`
- `notes`

Do not include unknown fields. Do not include any Chinese translation.
