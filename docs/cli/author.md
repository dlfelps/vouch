# Authoring: compare, suggest, sync, export, catalog

## `vouch compare`

The arithmetic between two values, and the code to cite it.

```console
$ vouch compare A B [--write] [--json] [--root DIR]
```

`--write` appends the generated `derive`/`claim` to the first values module
(refuses if either key is already defined there). For two Stats, also reports
the difference in pooled standard deviations and a Welch t-test p-value.
`better=` on the underlying values decides the winner.

```console
$ vouch compare cifar.resnet.acc cifar.vit.acc
cifar.resnet.acc 93.2% vs cifar.vit.acc 72.4%   (higher is better → cifar.resnet.acc is better)
  difference +0.208 (20.8 points) · ratio 1.287 · relative +28.7%
  Stat: 25 pooled std apart · Welch t-test p < 1e-6 (n = 5, 5)
paste into vouch_values.py (or run again with --write):
  @vouch.derive("cifar.resnet_vs_vit.pts", fmt=".1f", unit="points", better="higher",
                desc="cifar.resnet.acc minus cifar.vit.acc, percentage points")
  def _(v): return 100 * (v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"])

  @vouch.claim("cifar.resnet_beats_vit", desc="cifar.resnet.acc > cifar.vit.acc")
  def _(v): return vouch.gt(v["cifar.resnet.acc.mean"], v["cifar.vit.acc.mean"])
then cite:
  \vouchclaim{cifar.resnet_beats_vit}{ResNet-50 outperforms ViT-B} by \vouch{cifar.resnet_vs_vit.pts} points
```

## `vouch suggest`

Turn numbers typed into the paper into citations.

```console
$ vouch suggest [FILE] [--apply] [--json] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `FILE` | one tex file (default: every paper) |
| `--apply` | rewrite unique exact matches |

```console
$ vouch suggest paper/main.tex
main.tex:88   93.2\%    → \vouch{cifar.resnet.acc.mean}      exact (fmt .1pct)
main.tex:90   0.93      → \vouch[.2f]{cifar.resnet.acc.mean}  exact
main.tex:131  12.5\%    → NO SOURCE — no recorded value renders as 12.5% (nearest: cifar.vit.drop = 12.1%)
main.tex:140  3{,}014   → ambiguous: compl.words (3014), sweep.n_configs (3014) — choose by hand
4 literals: 2 replaceable (--apply), 1 no-source, 1 ambiguous
```

`--apply` rewrites only unique exact matches and keeps surrounding math
delimiters. Ambiguous and no-source literals are never touched.

## `vouch sync`

Write each cited value into a trailing `% vouch:` comment on its line, or
remove every one.

```console
$ vouch sync [--strip] [--root DIR]
```

Only takes effect with `[latex] annotate = true` in `vouch.toml` — see
[LaTeX interface](../guide/latex.md#source-annotations-opt-in-latexannotate-true).

## `vouch export`

Write the provenance table on demand.

```console
$ vouch export --csv PATH [--all] [--paper FILE] [--json] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `--csv PATH` | output path (default: stdout) |
| `--all` | include recorded keys the paper doesn't cite |
| `--paper FILE` | which paper (main `.tex`), if there are several |
| `--json` | rows as JSON instead of CSV |

See [Provenance CSV](../guide/provenance-csv.md) for the column reference.

## `vouch catalog`

Rewrite `.vouch/CATALOG.md` on demand (every `vouch build` also does this).

```console
$ vouch catalog [--root DIR]
```

The catalog is the file to hand an LLM agent writing the paper — see
[LLM & agents](../llm/index.md).
