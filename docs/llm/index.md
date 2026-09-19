# LLM & agents

*Summarizes [SPEC.md §13.1–13.4](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

vouch is built around one rule: **at every point where an agent would otherwise
guess, make the correct action cheaper than the guess.**

| Where an agent would guess | vouch gives it instead |
|---|---|
| "what was the accuracy again?" | `.vouch/CATALOG.md`, `vouch search`, `vouch cite` |
| writing the difference or ratio of two numbers | `vouch compare` → a `derive` |
| "A outperforms B" | `vouch compare` → a claim; `\vouchclaim` |
| a number that doesn't exist yet | `vouch.expect()` → `[pending: key]` |
| converting an existing paper | `vouch suggest --apply`, with `no-source` for numbers matching no record |
| knowing whether it slipped | the edit hook (immediate) and `check --strict --json` (a fix list) |
| a value changed under its prose | `vouch changes`, with the citing sentences |

## The catalog: `.vouch/CATALOG.md`

Written by every build and committed, the catalog lets an agent see everything
citable with a single file read and no command. It is kept token-lean: one line
per key, empty fields omitted.

```markdown
# vouch catalog — 38 values · 3 claims · 1 table · 1 pending · 2 changed
Cite: \vouch{key} · \vouch[fmt]{key} · \vouchclaim{key}{text} · \vouchtable{key}
Formats: .1f .2e ,d .1pct x=\times u=unit · Stat → mean \pm std · subfields .mean .std .n .ci95
Find: vouch search "words" · Snippet: vouch cite KEY · Arithmetic/claims: vouch compare A B · Missing: vouch.expect(...)
Never type a number. Never compute with numbers in prose.

## cifar (9)
cifar.resnet.acc · 93.2 ± 0.4% · top-1 test accuracy, mean ± std over seeds · higher↑ · cifar_resnet · fresh · cited 3×
cifar.vit.acc · 72.4 ± 1.1% · top-1 test accuracy, mean ± std over seeds · higher↑ · cifar_vit · CHANGED · cited 2×
…
## claims
cifar.resnet_beats_vit · HOLDS (margin 28.7%) · ResNet top-1 > ViT top-1
## tables
main · 3×3 · Model | CIFAR-10 | ImageNet · cells main.<model>.<column>
## pending (vouch todo)
imagenet.convnext.acc · ConvNeXt-T top-1 on ImageNet · run: python experiments/train.py --dataset imagenet --model convnext
## changed since last ack (vouch changes)
cifar.vit.acc · 91.2% → 72.4% · SUSPICIOUS (large move) · main.tex:118, main.tex:203
```

Projects with more than 300 keys get an index (prefix, count, one-line summary)
and one file per prefix under `.vouch/catalog/`. `vouch catalog` rewrites it on
demand.

## Lookup and insertion commands

**`vouch search`** ranks keys with BM25 over key segments, description, run id,
unit, a tracked call's function/arguments, and table columns — stdlib-only, no
embeddings. Pending (`vouch.expect`) keys are found too.

**`vouch cite`** returns the exact snippet, and enough context to write the
sentence correctly.

**`vouch compare`** does the arithmetic and writes the code that makes it
citable, including a Welch t-test for two Stats. `--write` appends the
definitions to the first values module.

**`vouch suggest`** converts numbers already written in the paper, and exposes
invented ones — see the [CLI reference](../cli/author.md#vouch-suggest).

## Placeholders instead of invention

When an agent drafting prose needs a number that doesn't exist, the approved
move is:

1. Write `\vouch{imagenet.convnext.acc}` in the prose.
2. Add `vouch.expect("imagenet.convnext.acc", desc=…, producer="…")` to `vouch_values.py`.
3. Tell the user the experiment is owed. `vouch todo` lists it.

The draft compiles with a visible `[pending: …]`. This is the most important
anti-hallucination feature in vouch: a legitimate move other than inventing a
number.

## While writing experiment code

- keys follow `<dataset>.<model>.<metric>`; one run id per configuration
- always a `desc`, and `better=` for any metric
- multi-seed results use `Stat.of(per_seed)` (or `record_all(..., stats=True)`), never a hand-computed mean
- prefer one `record_all(metrics, prefix=…)` at the end of an experiment over many scattered `record()` calls
- data goes through `run.input()`, outputs through `run.artifact()`; figures are saved inside the run
- parameters go through `params=`, so hyperparameters in the paper are citable

`vouch status --json` gives an agent the stale runs and their exact re-run
commands. `vouch todo --json` gives the owed experiments.

See also: [Claude Code](claude-code.md), [MCP server](mcp-server.md).
