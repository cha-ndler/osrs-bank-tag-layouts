# Security

## Reporting

Open a [security advisory](https://github.com/cha-ndler/osrs-bank-tag-layouts/security/advisories/new)
rather than a public issue. Please do not include exploit details in an issue.

## What this repository does and does not do

The published data is **item ids and grid positions**. Importing a layout
rearranges a bank tab in a third-party game client; it cannot run code, and it
carries no credentials or personal data.

The generator only ever **reads** from the OSRS Wiki over HTTPS. It sends no
user data, and it writes nothing outside this repository.

## How the repository is protected

- `main` is protected: pull requests are required, force pushes and branch
  deletion are blocked.
- CI runs the test suite and validates the published data on every pull request.
- GitHub Actions are pinned to **commit SHAs**, not tags. A tag can be moved to
  point at new code by a compromised upstream account; a SHA cannot.
- The default workflow token is read-only. Only the scheduled refresh job gets
  write access, and it opens a pull request rather than pushing to `main`.
- Dependabot proposes action and dependency updates weekly, so SHA pinning does
  not mean stale code.

## Reviewing an automated refresh

The weekly job regenerates the library from the wiki and opens a pull request.
Treat it as untrusted input — it reflects whatever the wiki said that morning:

- A large unexpected diff usually means an upstream template changed, not that
  the meta moved.
- `report.json` lists every item-name normalisation and any validation finding.
- CI enforces that the complete-ratio has not regressed and that both layout
  styles still describe the same items.
