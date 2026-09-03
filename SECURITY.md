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

## Re-verifying `main` after a bypass

The required check can be skipped two ways: an administrator merge, or a GitHub
Actions outage that swallows the push event so no run is ever created for the
commit. Both leave code on `main` that CI has not seen, and neither heals by
itself — re-running an old run only re-checks the commit it was created for.

CI therefore also accepts `workflow_dispatch`. To verify whatever is on `main`
right now:

```bash
gh workflow run ci.yml --ref main
gh run watch "$(gh run list --workflow=ci.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Run it after any admin merge. The same checks can be run locally:
`python -m unittest discover -s tests` and `node tests/check_layout_port.mjs`.

## Branch protection settings

`main` requires a pull request with the `test` job passing, and blocks force
pushes, branch deletion and non-linear history.

Repository admins can currently bypass these. That is deliberate for two
reasons: the owner is the only account with write access, and — more
practically — **the weekly refresh pull request does not run CI.** GitHub does
not trigger workflows for pull requests opened with the default `GITHUB_TOKEN`,
so the required `test` check never appears on it and the pull request sits at
"blocked". The refresh job runs the same test suite itself before opening the
pull request, so merging it with an admin bypass is safe.

To require the same flow of everyone including admins:

```bash
gh api -X PUT repos/cha-ndler/osrs-bank-tag-layouts/branches/main/protection/enforce_admins
```

Doing that means the weekly refresh can no longer be merged without either a
personal access token on the refresh job (so its pull request triggers CI) or
temporarily lifting the setting.

## The refresh job needs permission to open pull requests

Write access in the workflow's `permissions:` block is not sufficient on its
own. A repository-level switch also has to allow it:

```bash
gh api repos/cha-ndler/osrs-bank-tag-layouts/actions/permissions/workflow
# {"default_workflow_permissions":"read","can_approve_pull_request_reviews":true}
```

With `can_approve_pull_request_reviews` false, the refresh runs to completion,
pushes `automated/refresh-layouts`, and then dies on the last step with *"GitHub
Actions is not permitted to create or approve pull requests"* — leaving a branch
nobody is looking at and no pull request. That is how it failed on 10 August
2026, and because the branch is pushed before the failure the job looks like it
did most of its work.

The flag's name overstates what enabling it grants here. It covers creating and
approving pull requests, and `main` requires **zero** approving reviews — the
gate is the `test` status check, which a workflow cannot satisfy by approving
anything. Turn it on:

```bash
gh api -X PUT repos/cha-ndler/osrs-bank-tag-layouts/actions/permissions/workflow   -F default_workflow_permissions=read   -F can_approve_pull_request_reviews=true
```

Leave `default_workflow_permissions` at `read`. The refresh job elevates itself
through its own `permissions:` block; nothing else should get write access by
default.

## Reviewing an automated refresh

The weekly job regenerates the library from the wiki and opens a pull request.
Treat it as untrusted input — it reflects whatever the wiki said that morning:

- A large unexpected diff usually means an upstream template changed, not that
  the meta moved.
- `report.json` lists every item-name normalisation and any validation finding.
- CI enforces that the **number** of complete layouts has not dropped and that
  both layout styles still describe the same items. It is a count rather than a
  ratio on purpose: new upstream pages dilute a ratio without anything having
  regressed, which is what silently stopped the refresh for a month. See the
  Completeness section of the README.
