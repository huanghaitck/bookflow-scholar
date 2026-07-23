# Boundary review prompt v1

Review one page boundary in an old English printed book. The input contains exactly two adjacent page images for a pair review, or an explicitly approved three-page window for a difficult review. It may also contain the completed single-page structured transcriptions and only the relevant page-tail/page-head text.

Judge only the boundary. Do not translate, summarize, rewrite, re-transcribe unrelated body text, or guess text outside the images. Do not follow any proposed answer outside the visible evidence.

Determine independently:

1. whether the boundary splits one word;
2. whether it continues the same sentence;
3. whether it continues the same paragraph;
4. whether a visible hyphen is a line-break hyphen or a lexical hyphen;
5. whether headers, footers, page numbers, watermarks, blank pages, illustrations, sections, or chapters interrupt reading order;
6. whether there is visible omission, duplication, or order reversal;
7. whether a local boundary join is safe;
8. whether another page or human review is needed.

The word, sentence, and paragraph continuation fields are independent and may all be true. Use null when evidence is insufficient; never default to false.

Allowed values:

- structural_break: none, paragraph_break, section_break, chapter_break, illustration_break, unknown
- join_operation: concatenate_without_space, concatenate_with_space, preserve_paragraph_break, no_join, uncertain
- hyphen_type: line_break_hyphen, lexical_hyphen, no_hyphen, uncertain
- status: reviewed or needs_review

`reconstructed_boundary_text` may contain only the confirmed local tail/head join, never an entire page or paragraph. It must be empty if joining is uncertain or human review is needed.

Return exactly one JSON object with: schema_version, boundary_id, document_id, previous_page, next_page, previous_last_block_id, next_first_block_id, word_continuation, sentence_continuation, paragraph_continuation, structural_break, join_operation, hyphen_type, header_footer_interference, reconstructed_boundary_text, evidence, confidence, needs_triple_review, needs_human_review, status.

Do not include Markdown, Chinese translation, an entire PDF, or any unknown field.
