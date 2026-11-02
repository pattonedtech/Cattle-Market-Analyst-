# Security policy

This is a public static website and data repository. It does not provide user accounts, private cloud storage, or a shared authenticated submission API.

## Do not disclose secrets

Never commit access tokens, API keys, passwords, private customer data, financial account information, or private records. GitHub Actions secrets must be stored in repository settings, never in source files or logs.

## Reporting a vulnerability

Please report security issues through GitHub's private vulnerability reporting / Security Advisory feature if it is enabled for this repository. If private reporting is not enabled, contact a repository maintainer privately before publishing details. Do not post exploitable vulnerabilities or sensitive data in a public issue.

## Scope and safeguards

- Public pages and published data are intentionally readable.
- Browser-local observations are local to the visitor's browser and are not a secure backup or a private multi-user database.
- Shared changes require maintainer review and should be merged through protected pull requests.
- The scheduled data workflow uses a repository-scoped GitHub Actions token to publish generated data.
