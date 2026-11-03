# verified-release-action

The Verified Engineering release gate as a GitHub Action, **mirrored** from the canonical source
in `theplus-tech-knowledge` (`tools/mirror_release_action.py`). This repository exists so that
every ThePlus-Tech repository can run the gate without a read token to the knowledge base. It
holds execution code only — no documents, no data, no secrets.

Use, pinned to a full commit SHA of THIS repository:

```yaml
- uses: Thepluscode/verified-release-action/agent-config/ci/verified-release@<full-sha>
```

`MIRROR.json` names the canonical knowledge-base commit and the sha256 of every file. Do not edit
files here; change the canonical source, re-run the mirror, verify with `check`, commit.
