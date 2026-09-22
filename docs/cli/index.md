# CLI reference

Every command accepts `--json`, which emits a stable, versioned envelope
(`schema: vouch/1`), and `--root DIR`. Output uses ✓/✗ when the console
supports them and falls back to ASCII otherwise.

| Command | Does |
|---|---|
| [`vouch init`](setup.md#vouch-init) | Writes `vouch.toml`, detects the main `.tex`, copies `vouch.sty`, prints the `\usepackage` line to add. |
| [`vouch build`](setup.md#vouch-build) | Evaluates `vouch_values.py`, renders the values file, tables and catalog, reports changes. |
| [`vouch watch`](setup.md#vouch-watch) | Rebuild whenever a run records values or the paper or `vouch_values.py` changes; prints what moved. |
| [`vouch check`](check.md#vouch-check) | The gate. Read-only. Target: under 1 s. |
| [`vouch status`](inspect.md#vouch-status) | Freshness per run, with the exact re-run command. |
| [`vouch ls`](inspect.md#vouch-ls) | Keys with rendered value, description, run, freshness and citation count. |
| [`vouch trace`](inspect.md#vouch-trace) | The full provenance chain for a key, figure, script, or file:line. |
| [`vouch explore`](inspect.md#vouch-explore) | Browse every recorded value in a local web page; copy the LaTeX that cites it. |
| [`vouch search`](inspect.md#vouch-search) | Ranked lookup by words. |
| [`vouch cite`](inspect.md#vouch-cite) | The snippet to paste, plus its rendering. |
| [`vouch compare`](author.md#vouch-compare) | Arithmetic between two values, plus ready-to-paste `derive`/`claim` code. |
| [`vouch suggest`](author.md#vouch-suggest) | Match bare numbers already in the paper to keys. |
| [`vouch todo`](inspect.md#vouch-todo) | Pending `expect()` keys with their producer commands. |
| [`vouch changes`](check.md#vouch-changes) | Pending changes to cited values, with the citing sentences. |
| [`vouch diff`](check.md#vouch-diff) | Every recorded value that moved between git revisions (default: `HEAD` vs the working tree), with Δ, direction and better/worse. |
| [`vouch review`](check.md#vouch-review) | Interactive review of pending changes. |
| [`vouch ack`](check.md#vouch-ack) | Acknowledge changes. |
| [`vouch accept`](check.md#vouch-accept) | Record a reviewed staleness. |
| [`vouch export`](author.md#vouch-export) | Provenance table on demand. |
| [`vouch catalog`](author.md#vouch-catalog) | Regenerate `.vouch/CATALOG.md`. |
| [`vouch sync`](author.md#vouch-sync) | Refresh or remove source annotations. |
| [`vouch run`](integrations.md#vouch-run) | Record a run of any command. |
| [`vouch import`](integrations.md#vouch-import) | Register every value in an existing results file with one command. |
| [`vouch hook`](setup.md#vouch-hook) | Install the git pre-commit hook / the Claude Code hook entry point. |
| [`vouch mcp`](integrations.md#vouch-mcp) | Start the MCP server. |

## Sample outputs

```console
$ vouch status
runs: 4 fresh · 1 stale · 1 accepted
  ✓ cifar_resnet     fresh        2026-09-12 · 57 min
  ✗ cifar_vit        stale        src/models/vit.py::ViT.forward, src/data.py::augment changed
                                  re-run: python experiments/train.py --model vit
  ~ figures          accepted     "renamed axis label variable" (2026-09-14, Daniel Felps)
```

```console
$ vouch trace cifar.resnet.acc
cifar.resnet.acc = Stat(mean=0.93214, std=0.0041, n=5)   → "93.2 ± 0.4\%"   (fmt .1pct)
  desc      top-1 test accuracy on CIFAR-10, mean ± std over seeds   · better: higher
  recorded  experiments/train.py:88   in run cifar_resnet
  command   python experiments/train.py --model resnet50 --seeds 5
  when      2026-09-12 14:03 UTC · 57 min · git 0fdc530 (clean) · python 3.13.5, torch 2.5.1
  code      14 units in 4 files · fresh            (--code to list)
  inputs    data/cifar10.npz · fresh
  feeds     cifar.resnet_vs_vit.pts (derived) · claim cifar.resnet_beats_vit · table main[resnet, CIFAR-10]
  cited     paper/main.tex:41, paper/main.tex:118, paper/sections/results.tex:22
```
