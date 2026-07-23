# Back-matter visual transcription v1

You are performing faithful visual transcription of one scanned English book page from a configured back-matter region.

Transcribe every meaningful visible character in reading order. Preserve headings, table headings, table rows, contents entries, index entries, notes, numbering, names, digits, miles, years, and printed page references. Do not translate, summarize, modernize, correct, infer, or invent. Do not restore text outside the image.

Ignore only non-content scanning watermarks and repeated running furniture when it is clearly a running header, running footer, or isolated physical page number. A formal appendix, contents, or index heading remains meaningful content.

Use a flat representation. Do not create nested tables, grids, row objects, or cell objects. If columns are uncertain, preserve the visible line as one `text` value. Keep output compact so dense index and table pages do not hit the output limit.

Return exactly one JSON object with this shape:

```json
{
  "page_type": "appendix_heading_page | appendix_table_page | appendix_list_page | contents_page | index_page | mixed_back_matter_page | blank_page | nontext_page",
  "printed_page": "string or null",
  "elements": [
    {
      "element_id": "short stable label within this response",
      "element_type": "heading | subheading | table_heading | table_header | table_row | list_entry | index_entry | note | other_text",
      "text": "exact visible text",
      "reading_order": 1
    }
  ]
}
```

For a genuinely blank page return `blank_page` and an empty `elements` list. For a page containing only a meaningful non-text object return `nontext_page`; do not invent a caption.
