# verified-release — the release gate as a CI job

Consume it from `Thepluscode/verified-release-action` — a public, hash-verified mirror of this
directory and the two tools it runs (`tools/mirror_release_action.py`). Never from this knowledge
base, which stays private. Pin to a **full commit SHA** of the action repository:

```yaml
permissions:
  contents: read        # the contract's commands run with a read-only token and no secrets
  pull-requests: read   # Tier C exemptions are checked against APPROVED PR reviews
jobs:
  verified-release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Thepluscode/verified-release-action/agent-config/ci/verified-release@<full-sha>
  deploy:
    needs: verified-release        # cannot start on BLOCK or INSUFFICIENT_EVIDENCE
    ...
```

Exit 0 PASS · 1 BLOCK · 2 INSUFFICIENT_EVIDENCE. The evidence package is uploaded as a workflow
artifact bound to the exact commit. Tier C `not_applicable` exemptions must be APPROVED on a pull
request by a login in the `approvers` input (default `Thepluscode`); a name in the contract is not
an approval.

The contract's own commands need the repository's toolchain on the runner (set up Python, Node,
Java etc. before the step, as the repository's existing CI already does). Where production
deploys from the Git push itself (Vercel, Railway Git integration), a job that runs after the
push is not a gate: route production to a branch that only this job may advance, or use the
provider's wait-for-checks control; both are per-repository founder decisions recorded in
`knowledge-base/engineering-coverage-findings.md`.
