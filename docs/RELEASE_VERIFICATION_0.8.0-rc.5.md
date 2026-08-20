# Release verification: 0.8.0-rc.5

## Primary SHA-256

- Installer: `cfca61023acef233b6bc1a929d19c7c0bf8bbd23ba6f7c89f4e0b8d96a3b9ed1`
- Portable ZIP: `69a55853bc07bfb509e554a7ab65dd54dad761e45299620c206c9f4146345431`

## Verified

- Dedicated Python 3.12.13 environment gate passed.
- 92 relevant production, damaged-page, Web Assist, multilingual, batch, and contract tests passed.
- A bad OCR page exported a non-empty review-only object template with stable IDs and hashes.
- Corrected source and translation validated, previewed, applied at object level, rebuilt outputs, and reduced the review count from one to zero.
- Unstructured whole-page text remained blocked from automatic application.
- TypeScript/Vite production build and Tauri release build passed.
- Portable extract-and-launch passed.
- Current-user installer installed and launched from the installed directory; uninstall removed program files and preserved all 253 existing user-data files.
- Published-file hashes recomputed successfully; source paths, user-profile paths, `BOOKFLOW_PYTHON`, and API-key patterns were absent.

## Not claimed

The original external 1863 PDF was not available, and real Provider credentials were absent on the build machine. Exact historical-font OCR quality and real GLM/DeepSeek execution remain external retest items.
