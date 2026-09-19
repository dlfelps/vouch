# Concepts

*Summarizes [SPEC.md §1–2](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

## Why vouch exists

Numbers in papers go wrong in four ways:

1. **Typed wrong.** Transcription errors, stale copies, or rounding done by hand.
2. **Never produced.** Written from memory or a draft, or hallucinated by an LLM.
3. **Out of date.** The code changed or the experiment was re-run, and the paper didn't follow.
4. **Prose invalidated.** The number was updated correctly, but the sentence around it ("the best of all models", "roughly doubles") is now false.

vouch's predecessor, a hand-maintained registry for a single manuscript, handled
1–3 but treated problem 4 as an afterthought. vouch is built to close all four,
by design:

- Record values where they are computed, instead of writing an extractor per number.
- Insert values, instead of typing them and then checking them.
- Hash code, instead of dating commits.
- Track changes to the functions a run actually executed, instead of whole files.
- Treat prose invalidation as a first-class concern.

## Goals

| # | Goal |
|---|---|
| G1 | No hand-typed empirical numbers. Every number in the paper is inserted from a recorded value. |
| G2 | Every value traces to code (file:line), run, command, parameters, inputs and commit, from the terminal, the PDF (hover) or a CSV. |
| G3 | A result whose code changed since it was produced is detected, precisely enough that a formatting or unrelated edit never triggers a false alarm. |
| G4 | When a cited value changes, the sentences that cite it are surfaced and stay flagged until reviewed. |
| G5 | An LLM agent can find, cite, compare and verify values without guessing, and gets immediate feedback when it slips. |
| G6 | Adopting it is cheap: one `record_all()` call per experiment (or one `record()` per value), and experiments run exactly as before. |
| G7 | `vouch check` finishes in under a second, fast enough for pre-commit, and never re-runs experiments. |
| G8 | Output is plain LaTeX that works in local builds, Overleaf and arXiv. |
| G9 | The core has no third-party dependencies (Python ≥ 3.10; `tomli` on 3.10 only). |

## Non-goals (v1)

- Orchestrating or scheduling experiments. vouch is not DVC, Snakemake or Make.
- Re-running experiments automatically.
- Storing large artifacts. Only their hashes are stored; the files live wherever you keep them.
- Jupyter notebooks as tracked code (explicit runs work in notebooks, but cell code is not hashed).
- A typed-and-checked mode (`\vouch{key}{93.2\%}`).
- Markdown, Typst or HTML output.

## Vocabulary

| Term | Meaning |
|---|---|
| **Project** | The directory containing `vouch.toml`. All paths are stored relative to it, with forward slashes. |
| **Run** | One successful execution of an experiment, identified by a **run id**. The latest successful run per id is the *run of record*, stored in `.vouch/runs/<id>.json`. |
| **Value** | A recorded quantity with a **key**, a raw value, and metadata (format, unit, description, direction, call site). |
| **Key** | The name a value is recorded under **and** cited by. Keys are literal: the key you record is the key you cite. |
| **Claim** | A recorded or derived boolean with prose attached in the paper (`\vouchclaim`). It fails the check if it becomes false. |
| **Derived value** | A value computed from other values or artifacts by a function in `vouch_values.py`, evaluated by `vouch build`. |
| **Expected value** | A placeholder declared with `vouch.expect()` for a number the paper needs but no run has produced yet. |
| **Artifact** | A file a run produced (data, figure, table source, model), identified by path and content hash. |
| **Input** | A file or directory a run consumed. It is hashed, and a change makes the run stale. |
| **Code unit** | A piece of first-party code a run depended on: a function, a class body, or module-level code. It is identified as `path::qualname` and hashed semantically. |
| **Fresh / stale** | Whether a run's recorded code units and inputs still match the working tree. |
| **Acknowledged baseline** | The last reviewed value of each cited key. A difference from it is a *pending change*. |
| **Citation** | A use of a key in the paper's LaTeX (`\vouch`, `\vouchclaim`, `\vouchtable`, `\includegraphics`). |

## Key grammar

```
key      := segment ("." segment)*          max 128 chars, case-sensitive
segment  := [A-Za-z0-9_-]+
```

Recommended convention: `<dataset>.<model>.<metric>`, e.g. `cifar.resnet.acc` or `imagenet.vit_b16.top5`.

Some keys are generated rather than recorded:

| Generated key | Source |
|---|---|
| `<key>.mean`, `.std`, `.n`, `.ci95`, `.min`, `.max` | subfields of a `Stat` value |
| `<run>.param.<name>` | run parameters (nested dicts are flattened with dots) |
| `<table>.<row>.<col>` | table cells; row and column names are slugified to `[A-Za-z0-9_-]` |

Two sources producing the same key is a `key-conflict` error.
