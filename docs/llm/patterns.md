# Patterns for agents

*Usage, not design: [SPEC.md](https://github.com/dlfelps/vouch/blob/main/SPEC.md) stays
authoritative for what each call does.*

These are the moves that work when an agent (Claude Code, with the skill and the edit
hook from `vouch init --agents`) writes the experiments and the paper together. Each
one replaces a guess with a recorded value, a derivation, or a placeholder.

## The loop

1. **Record** the result where it is computed, in the experiment.
2. **Run** the experiment. Every `vouch.run` writes `.vouch/runs/<id>.json`.
3. **Define** any number computed from recorded numbers in `vouch_values.py`.
4. **Cite** in the `.tex`: paste what `vouch cite KEY` prints. The edit hook checks
   each edit at once.
5. **`vouch build`**, then **`vouch check`**. Both must pass before a commit;
   `vouch check --strict` only at submission, since it also fails on owed
   `vouch.expect()` values.

Define before you cite. The hook knows keys from runs recorded since the last build
and definitions added to `vouch_values.py` since then. A citation written before
either exists is reported as unknown, correctly, until it does.

## One run per configuration

A long sweep keeps its progress when each configuration is its own run:

```python
for n in (8, 16, 32, 64):
    with vouch.run(f"scaling_n{n}", params={"n": n}):
        vouch.record(f"scaling.n{n}.seconds", t, fmt=".2f", unit="s",
                     desc="wall time of the solver at n qubits")
```

One `with vouch.run("battery"):` around a whole experiment is right when the
experiment is quick and its values belong together. Data the run reads goes through
`vouch.input(path)` and files it writes through `vouch.artifact(path)`, so
freshness covers them; a matplotlib figure saved inside the run is tracked on its
own.

## Evidence goes in the run, as a claim

A property the experiment checks ("no certified pair has a nonzero gradient") is a
claim recorded where it is checked, with the numbers behind it:

```python
vouch.claim("battery.sound", false == 0,
            desc="no certified pair has a measured nonzero gradient",
            values={"false": false, "certified": len(certified)})
```

The prose wraps it: `\vouchclaim{battery.sound}{No certified pair has a measured
nonzero gradient.}` If a re-run breaks it, `vouch check` fails, which is the point.

## Tables: record once, cite cells

```python
vouch.table("battery.stages", rows, columns=["id", "stage", "pairs", "residual"],
            row_key="id", desc="pairs by the stage that first certifies them")
```

`\vouchtable{battery.stages}` sets the whole body. Every cell is a key of its own,
`<table>.<row>.<col>`, with row and column names slugified, so a hand-written
`tabular` that needs only some columns cites cells:
`\vouch[,d]{battery.stages.hull.pairs}`. The format in brackets overrides the
recorded one for that citation only.

## Numbers from numbers, and from someone else's numbers

Every share, ratio, sum or comparison is a definition in `vouch_values.py`, never
arithmetic in prose:

```python
@vouch.derive("battery.ratio", fmt=".2fx",
              desc="pairs the new walk certifies over those the old one certifies")
def _(v):
    return v["battery.affine"] / v["battery.span"]
```

`vouch compare A B --write` writes the common ones for you. A definition can read a
file too, and a claim can hold a new result to a published one:

```python
@vouch.derive("baseline.certified", fmt=",d", inputs=["vendor/data/pairs.csv"],
              desc="pairs the published walk certifies")
def _(v, rows):
    return sum(r["forward_dead"] == "True" for r in rows)

@vouch.claim("battery.matches_baseline", desc="the exact count is the published one")
def _(v):
    return v["battery.exact"] == v["baseline.certified"]
```

A table is readable in a definition as its rows, `v["yield"]`, a list of dicts by
column, which is how a summary over many cases ("in every structured case") stays a
claim rather than a sentence.

## Formats that read like prose

| you want | write |
|---|---|
| "two qubits", "seven experiments" | `\vouch[w]{key}`: zero to ten as words, larger as digits |
| 37,920 | `\vouch[,d]{key}` |
| 95%, 94.6% | `.0pct`, `.1pct` on a fraction |
| 1.90× | `.2fx` on a ratio |
| a small float | `.1e` |

Set the usual format at record time (`fmt=`) or for a family of keys in
`vouch.toml` `[metrics]`, and override it per citation in brackets.

## Owed numbers

A number no run has produced yet is cited anyway and declared:

```python
vouch.expect("imagenet.vit.acc", desc="ViT top-1 on ImageNet",
             producer="python train.py --dataset imagenet")
```

The draft prints `\vouchpending{key}` (a boxed `pending: key` unless the paper
redefines it), `vouch todo` lists what is owed, and the agent tells the user. It is
never replaced by an invented number.

## When values move

After a re-run, `vouch build` and `vouch check` list every cited value that moved,
with the sentences that cite it. Re-read each sentence and fix what the new value
makes wrong. A move marked **SUSPICIOUS** (a large relative change, a sign flip, a
move to or from zero, or the same value now produced elsewhere) goes to the user,
who may see a bug; acknowledging (`vouch ack`, `vouch review`) is theirs. A claim
that no longer holds is not a warning: `vouch check` fails. `vouch diff` summarizes everything a re-run changed, cited or not, against
the last commit.

When an experiment is redefined rather than re-run, give its keys new names. A key
that keeps its name but changes its meaning shows up as one value that moved, often
suspiciously, and its old baseline means nothing; a new key starts its own. Remove
the old citations, and `vouch check --verbose` lists the recorded values nobody
cites any more.

## The edit hook's messages

| it says | do |
|---|---|
| `93.2% is typed by hand; it is KEY: replace it with \vouch[.1pct]{KEY}` | paste the replacement |
| `... matches no recorded value` | record it, derive it, or `vouch.expect()` it; tell the user |
| `unknown key K ... (did you mean ...)` | a typo, or a key not defined yet: define it first |
| `\vouchclaim takes two arguments` | add the prose the claim vouches for |

Coordinates inside drawing environments (`tikzpicture`, `axis`, ...) are not
results; node text is still prose and still checked.

## Running vouch in a uv project

In a uv project the agent's shell may not have `vouch` on `PATH`; use
`uv run --no-sync vouch ...`, which is also the hook command `vouch init --agents`
writes there. Never edit `.vouch/` or the generated `vouch-values.tex`,
`vouch-tables/` and provenance CSV by hand; `vouch build` writes them.

See also: [LLM & agents](index.md), [Claude Code](claude-code.md).
