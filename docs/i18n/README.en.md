# Bookflow Scholar User Guide (English)

[Download](https://github.com/huanghaitck/bookflow-scholar/releases/tag/v0.8.0-rc.2) · [Report a problem](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) · [Roadmap to 1.0](../ROADMAP_1.0.md) · [Home](../../README.md)

## What it does

Bookflow Scholar is a Windows desktop translator and layout reconstructor for papers, books, and monographs. It restores complete logical units across page breaks, translates them as a whole, and reinserts `【original page】` markers at the true boundaries. Deterministic document processing handles repeatable mechanics; multimodal models assist with layout and visual objects that rules alone cannot reliably classify.

Key improvements:

- body text, headers, footers, footnotes, and endnotes are segmented, translated, and reconstructed independently;
- images, maps, figures, captions, and tables are placed from document context, while unrelated copyright artwork can be excluded;
- glossary changes are scoped to source, translation unit, and exact occurrence/span;
- difficult-page answers flow back at object level without overwriting a whole page;
- source, target-language, and bilingual editions receive dynamic book-and-language filenames;
- pause, resume, resume after restart, cancel, and failure retry are available;
- Overview previews the final PDF with previous, next, and direct page navigation;
- Simplified Chinese, English, French, German, Japanese, and Spanish are supported; all 30 directed pairs have been covered.

## Start from an empty application

1. Install `Bookflow-Scholar-0.8.0-rc.2-setup.exe`, or extract the portable ZIP and run `Bookflow Scholar.exe`.
2. Select **Create project**. A project must exist before an imported PDF has a workspace and active context.
3. Open the project. Configure the text and vision providers, model names, and API keys, then save. Keys go to Windows Credential Manager, not project files.
4. Select **Import PDF**, then choose the source and target languages. In a multi-source project, explicitly select the active source.
5. Select **Start**. The progress view shows the active stage. You may pause, resume, cancel, or restart the app and continue.
6. When processing completes, inspect the final PDF in Overview. Use Previous, Next, or the `current/total` page control.
7. Glossary and difficult-page packages are exported only when candidates exist. Extract the ZIP, follow its official target-language prompt, then import the completed package.
8. Select **Open output folder** to access the source, target-language, and bilingual editions.

## Installation and safety

This release candidate is unsigned, so Windows may display SmartScreen. Verify the SHA-256 published on the Release page before running it, or use the portable ZIP. [Download LibreOffice from its official site](https://www.libreoffice.org/download/); it is optional but recommended for the validated office-document rendering path.

Do not attach confidential or copyrighted source documents to public feedback. Never paste API keys, authorization headers, private paths, or personal information. Use the free [GitHub problem form](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml).
