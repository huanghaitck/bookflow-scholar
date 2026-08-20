# Release verification: 0.8.0-rc.4

## Published hashes

| File | SHA-256 |
|---|---|
| `Bookflow-Scholar-0.8.0-rc.4-setup.exe` | `2c88b97969b4d55536e81c791d660a94dfa787673549b349e276e4bf3acebeb3` |
| `Bookflow-Scholar-0.8.0-rc.4-portable-win-x64.zip` | `3bcac79055592415790e58f938e4642cdc508f1115304387309c407f784cb03f` |
| `sidecar-manifest.json` | `7c05c4d366d199fa6519e179972bc6c0066ed0a068605b2c22d8f3ec186ce99d` |
| `runtime-prerequisites.json` | `7470b9374aecf69f96f07deef8dbbc006608799065a81ad77bf85a85e9a72de9` |
| `sbom.cdx.json` | `c1f215909e3da5ba39956c7d0331ca048ea688d32f0ee86ccf613b5a1f32bd4a` |
| `THIRD_PARTY_LICENSES.md` | `e55376a1ce5c2c20aa6717668b55d40e182a0aed729f63cd2246d58807e442bf` |

The complete authoritative list is `SHA256SUMS.txt` in the release assets.

## Checks completed

- Environment gate passed with `C:\Users\huanghai\.conda\envs\bilingual-book\python.exe`, Python 3.12.13.
- OCR/recovery, production-pipeline, multilingual, batch-backend, and backend-contract target suite: 98 passed.
- TypeScript/Vite production build and Tauri release build: passed.
- Two-page damaged-text regression: healthy page translated; rejected page excluded from provider dispatch and added to review; job completed.
- Two-page raster-scan fallback run: progress events advanced through workspace, per-page text quality, structure, planning, per-batch translation, rendering, validation, and completion; final progress 100%; one review page; output generated.
- Optional structure-provider failure: deterministic structure fallback completed the book and recorded `structure_provider_unavailable` for review.
- Portable extract-and-launch smoke: passed.
- Current-user installer install, installed-directory startup, and silent uninstall: passed.
- Uninstall removed program files and preserved all 253 pre-existing user-data files.
- Final hashes recomputed successfully; release scan found no source-tree path, user-profile path, `BOOKFLOW_PYTHON`, or API-key pattern.

## Provider boundary

The build machine's API credentials had previously been removed. A requested real-provider acceptance reached credential preflight, made zero network calls, wrote zero provider attempts, and incurred no usage. Therefore this report does not claim current real GLM/DeepSeek success. The external tester must configure their own credentials and retest the original scanned PDF.

## Remaining external confirmation

- Retest the original 1863 scanned PDF, which was not supplied to this workspace.
- Confirm OCR/VLM accuracy on its affected pages; rc.4 guarantees isolation and review routing when recognition remains unacceptable, not automatic recovery of every historical font.
- Confirm the visible page/unit progress and review count on the tester's Windows machine.
