# Bookflow Scholar 0.8.0-rc.2

Release candidate for Windows desktop translation and reconstruction of scholarly PDFs, books, and monographs.

## Highlights

- Cross-page logical units are restored before whole-unit translation, with original-page markers reinserted at real boundaries.
- Body text, headers, footers, footnotes, and endnotes use separate translation and reconstruction paths.
- Multimodal layout review complements deterministic PDF processing.
- Images, maps, figures, captions, tables, and reading order are reconstructed as document objects.
- Glossary application is scoped by source, translation unit, and occurrence/span.
- Difficult-page answers flow back at object level and cannot silently overwrite an entire page.
- Source, target-language, and bilingual outputs use dynamic book-and-language names.
- Real desktop controls cover start, pause, resume, resume after restart, cancel, and failure retry.
- Final-PDF overview supports previous/next navigation and direct page jumps for long books.
- Six languages and all 30 directed pairs are covered: Simplified Chinese, English, French, German, Japanese, and Spanish.
- Production behavior contains no branches tied to the regression book, fixture text, page count, absolute path, or test UUID.

## Downloads

- `Bookflow-Scholar-0.8.0-rc.2-setup.exe`: current-user installer.
- `Bookflow-Scholar-0.8.0-rc.2-portable-win-x64.zip`: extract-and-run package.
- `SHA256SUMS.txt`: integrity hashes.
- `sbom.cdx.json` and `THIRD_PARTY_LICENSES.md`: component and license inventories.

This candidate is unsigned. Windows may display SmartScreen. Verify SHA-256 before running either distribution.

## Six-language summary

- **简体中文：** 跨页整体翻译、对象级版面重建、独立脚注/尾注、动态三版本输出和可续跑桌面流程。
- **English:** cross-page translation, object-level reconstruction, independent notes, dynamic three-edition output, and resumable desktop processing.
- **Français :** traduction interpages, reconstruction par objet, notes indépendantes, trois éditions dynamiques et traitement reprenable.
- **Deutsch:** seitenübergreifende Übersetzung, objektbezogene Rekonstruktion, getrennte Anmerkungen, drei dynamische Ausgaben und fortsetzbare Verarbeitung.
- **日本語：** 改ページをまたぐ翻訳、オブジェクト単位の再構築、注記の独立処理、動的な3版出力、再開可能なデスクトップ処理。
- **Español:** traducción entre páginas, reconstrucción por objeto, notas independientes, tres ediciones dinámicas y proceso reanudable.

See the [six-language manuals](../README.md) and the [estimated 1.0 roadmap](ROADMAP_1.0.md).
