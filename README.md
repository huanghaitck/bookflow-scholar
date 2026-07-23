# Bookflow Scholar

Desktop translation and reconstruction for scholarly PDFs, books, and monographs.

**Languages:** [简体中文](docs/i18n/README.zh-Hans.md) · [English](docs/i18n/README.en.md) · [Français](docs/i18n/README.fr.md) · [Deutsch](docs/i18n/README.de.md) · [日本語](docs/i18n/README.ja.md) · [Español](docs/i18n/README.es.md)

[Download 0.8.0-rc.2](https://github.com/huanghaitck/bookflow-scholar/releases/tag/v0.8.0-rc.2) · [Verify release](docs/RELEASE_VERIFICATION_0.8.0-rc.2.md) · [Report a problem](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) · [Roadmap to 1.0](docs/ROADMAP_1.0.md)

Bookflow Scholar restores cross-page logical units before translation, then puts original-page markers back at their real boundaries. It combines deterministic document processing with multimodal layout analysis, keeps headers, footers, footnotes, and endnotes separate from body text, and reconstructs figures, captions, tables, and reading order instead of treating every PDF as a flat text stream.

The current release supports Simplified Chinese, English, French, German, Japanese, and Spanish. All 30 directed language pairs have been exercised with real desktop workflows. The product does not hard-code test books, page counts, fixture text, paths, IDs, or sample layouts.

## Quick start

1. Install `Bookflow-Scholar-0.8.0-rc.2-setup.exe`, or extract the portable ZIP.
2. Open Bookflow Scholar and select **Create project**.
3. Open the project, configure the text and vision providers, and save the credentials.
4. Import a PDF and select the source and target languages.
5. Select **Start**. Long jobs can be paused, resumed after restart, retried, or cancelled.
6. Review the overview, glossary package, and difficult-page package when present.
7. Open the output folder to find source, target-language, and bilingual editions with dynamic filenames.

Provider keys are stored through Windows Credential Manager. They must never be committed to this repository, included in an issue, or copied into logs. [LibreOffice](https://www.libreoffice.org/download/) is optional but recommended for the validated office-document rendering path.

This release is unsigned. Windows may show a SmartScreen warning; verify the published SHA-256 before running it. The portable ZIP is provided for users who prefer not to run an installer.

## Release status

`0.8.0-rc.2` is a release candidate. H4, S11, and S12 desktop acceptance are complete. Please use the structured [problem report form](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) for reproducible feedback, without confidential documents or credentials.

Copyright © 2026 huanghaitck. Source availability does not grant redistribution or commercial-use rights unless a separate license says otherwise.
