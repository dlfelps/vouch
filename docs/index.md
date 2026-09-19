# vouch

**Every number in your paper, vouched for by the code that produced it.**

Experiments **record** their results as they run. The paper **cites** them by key,
`\vouch{key}`, and never contains a typed number. `vouch check` proves that every
cited value exists, comes from a run whose code hasn't changed since, and that
every claim still holds. When a cited value changes, vouch shows you the sentences
to re-read. Any number traces back to the function, arguments, seeds, command and
commit that produced it, from the terminal, a local web page, or by clicking it in
the PDF.

It is built for people and for LLM agents writing papers: the correct number is
always cheaper to use than a guessed one.

[Start the tutorial](tutorial/index.md){ .md-button .md-button--primary }
[Quickstart](quickstart.md){ .md-button }

## Install

```console
$ pip install -e .          # from a clone; Python ≥ 3.10, no dependencies
$ vouch init                # finds your paper, writes vouch.toml, copies vouch.sty
```

The base install is everything, the MCP server included, with no dependencies
(only `tomli` on Python 3.10). Extras: `.[pandas]` to record pandas DataFrames;
`.[mcp]` installs nothing more (`vouch mcp` needs nothing); `.[test]` for the test
suite.

## The shape of it

| Step | What happens |
|---|---|
| **Record** | Decorate the function that computes your results (`@vouch.track`), or call `vouch.record`/`vouch.record_all` — every call is recorded with its arguments, seeds, timing and the exact code that ran. |
| **Find the key** | `vouch explore --open` browses every recorded value in a local page; `vouch search`/`vouch cite` do the same from the terminal. |
| **Cite** | `\vouch{key}` in the LaTeX source. `vouch build` renders it; `latexmk` compiles as usual. |
| **Compute** | Differences, ratios and derived tables live in `vouch_values.py`, evaluated by `vouch build` — never in anyone's head. |
| **Check** | `vouch check` proves every cited value exists, is fresh, and every claim still holds — in under a second, with no experiment re-run. |

## Why

Numbers in papers go wrong in four ways: they're typed wrong, never produced, out
of date after the code changed, or the number was updated correctly but the prose
around it ("the best of all models") is now false. vouch is built to close all
four, for a human author and an LLM agent alike — see [Concepts](guide/concepts.md)
for the full motivation.

Looking for exhaustive, implementation-level detail? The
[full design spec](https://github.com/dlfelps/vouch/blob/main/SPEC.md) in the
repository is authoritative; this site is a guided, web-friendly tour of the same
material.
