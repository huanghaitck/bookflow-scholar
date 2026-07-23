# Security and privacy

## Reporting

Use the [structured problem form](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) for ordinary defects. Do not publish API keys, authorization headers, confidential documents, private filesystem paths, or personal information.

For a vulnerability that cannot be described safely in public, open a minimal issue stating that private coordination is required, without exploit details or secrets.

## Provider credentials

The desktop application stores provider credentials through Windows Credential Manager. Credentials must not be stored in the repository, project package, exported review package, logs, diagnostics, or release artifacts.

If a credential is accidentally disclosed, revoke it at the provider immediately. Removing it from Git history does not make an exposed key safe again.

## Release verification

Version `0.8.0-rc.2` is unsigned. Download it only from this repository's Releases page and compare the file against `SHA256SUMS.txt`. A mismatch means the file must not be run.
