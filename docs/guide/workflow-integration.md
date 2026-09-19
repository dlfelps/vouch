# Workflow integration

*Summarizes [SPEC.md §15](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

## Git

- Commit everything except `.vouch/cache/`.
- `vouch init` adds `.gitattributes` entries marking generated files `linguist-generated=true`.
- After a merge conflict in a generated file, take either side and run `vouch build`.
- Run records are one file per run id, so parallel experiment branches rarely conflict.

## Pre-commit

- `vouch hook install` writes `.git/hooks/pre-commit`, honoring `core.hooksPath`. It runs `vouch check --quiet`, plus `--strict` if `[hook] strict`.
- The vouch repo also ships a `.pre-commit-hooks.yaml` (`id: vouch-check`) for the [pre-commit framework](https://pre-commit.com/).
- The hook must stay fast enough that nobody reaches for `--no-verify` — `vouch check` targets under a second.

## CI

```yaml
# .github/workflows/paper.yml
name: paper
on: [push, pull_request]
jobs:
  vouch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e path/to/vouch     # wherever vouch comes from
      - run: vouch check --strict
```

## Overleaf

- Everything LaTeX needs lives in the paper directory: `vouch.sty`, the values file and the tables. These sync through Overleaf's git bridge.
- Co-authors editing on Overleaf cite existing keys (from the committed catalog); unknown keys show `??key`.
- The build and check run locally or in CI.

## arXiv and camera-ready

Use `\usepackage[final]{vouch}` and upload `vouch.sty` plus the generated files.
`final` loads no extra packages and leaks no paths.

## Clusters

Records are plain files. Copy `.vouch/runs/<id>.json` (and any artifacts) back
from the cluster. Set `VOUCH_ROOT` if jobs run from another working directory.
Paths are always stored relative to the project root.

See also: [`vouch hook`](../cli/setup.md), [Checks and lints](checks-and-lints.md).
