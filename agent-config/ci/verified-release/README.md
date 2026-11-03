# verified-release — the release gate as a CI job

Use from any Thepluscode repository (the knowledge base grants same-owner Actions access):

```yaml
jobs:
  verified-release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Thepluscode/theplus-tech-knowledge/agent-config/ci/verified-release@main
  deploy:
    needs: verified-release        # cannot start on BLOCK or INSUFFICIENT_EVIDENCE
    ...
```

The contract's own commands need the repository's toolchain on the runner (set up Python,
Node, Java etc. before the step, as the repository's existing CI already does). Where
production deploys from the Git push itself (Vercel, Railway Git integration), a job that
runs after the push is not a gate: route production to a branch that only this job may
advance, or use the provider's wait-for-checks control (Railway "Wait for CI"); both are
per-repository founder decisions recorded in `knowledge-base/engineering-coverage-findings.md`.
