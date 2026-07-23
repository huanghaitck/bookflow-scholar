# Release verification: 0.8.0-rc.2

## Final Windows artifacts

| Artifact | SHA-256 |
|---|---|
| `Bookflow-Scholar-0.8.0-rc.2-setup.exe` | `847451888b6733f8f734b8ff9670065bfb5ca638b923518e8caf3f3c3d10f859` |
| `Bookflow-Scholar-0.8.0-rc.2-portable-win-x64.zip` | `60908136e4f636fe997847bca9c75a2c04ee0e9055e872d08c58b21a7562af05` |

The artifacts are intentionally unsigned. Verify these hashes against the attached `SHA256SUMS.txt` before running them.

## Checks completed

- H4 manual acceptance, S11 packaging, and S12 installed desktop acceptance: passed.
- Final publisher metadata: `huanghaitck`.
- Environment gate: Python 3.12 dedicated environment passed.
- Targeted backend and contract tests: 13 passed.
- Installer silent install, installed launch, registry publisher, and silent uninstall: passed.
- Portable archive layout, LibreOffice official link, extraction, and launch: passed.
- Installed program directory removed after the final smoke test; user projects remained in `%LOCALAPPDATA%\Bookflow Scholar\`.
- Final installer and portable hashes matched `SHA256SUMS.txt`.
- Public Git index contained no `.env`, local provider config, output, data, user input, personal absolute path, or real API credential.

Two secret-pattern matches remain deliberately in tests. They are inert redaction sentinels used to prove that authorization headers and API-like strings do not reach diagnostics.
