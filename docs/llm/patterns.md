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

Define before you cite. The hook knows keys from runs recorded since the last build,
definitions added to `vouch_values.py` since then, and keys a script records by a
literal name (`vouch.record("k", ...)`) that no run has recorded yet. Those are owed,
not unknown: a line just added, or a run still going, is enough. A citation of anything
else is reported as unknown, correctly, until it exists.

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

**Split a slow part from a quick one.** A run is the whole block, so adding one
quick claim to a script whose other half takes twenty minutes re-runs the twenty
minutes. Give each part its own run, with its body in a function, and skip the slow run
while its record stands:

```python
def search():                     # the slow, exhaustive part
    vouch.record("examples.search.left", count_left(), desc="...")

def examples():                   # the worked examples: milliseconds
    vouch.claim("examples.cascade", table_matches(), desc="...")

if not vouch.fresh("examples_search"):
    with vouch.run("examples_search"):
        search()
with vouch.run("examples"):
    examples()
```

The functions matter. Freshness is per function, and each run records the functions
it executed itself (and whatever ran outside every run), not those the other run did, in
whichever order they run. So an edit inside `examples()` leaves the slow run fresh, and an
edit inside `search()` leaves the quick one fresh; the same edit written inline at module
level changes the script's `<module>` unit, which both runs executed, and both go stale. `vouch.fresh`
is for runs whose result their code, inputs and parameters determine (seeded); pass
`params=` when those can change. While a run is going, `vouch status` shows it as
running, with its process, rather than as stale; a run killed before it ended shows
as interrupted.

A figure drawn from a slow run's numbers is a quick run of its own. It reads them with
`vouch.value(key)` (a number, a `Stat`, a claim's bool, a table's rows), which records
the read: relabelling the figure then re-draws it without re-measuring, and the figure
run goes stale only when a value it drew moves.

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

A number no run has produced yet is cited anyway. If the experiment already records
it by name, `vouch.record("imagenet.vit.acc", ...)` in a script that has not run since
that line was added, nothing more is needed: the key is pending on that script, and
`vouch todo` names it. Otherwise declare it:

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
| `unknown key K ... (did you mean ...)` | a typo: take the suggestion only if it is the key you meant |
| `unknown key K ...; find it with vouch search` | not near any key: search for it, or record or define it first |
| `\vouchclaim takes two arguments` | add the prose the claim vouches for |
| (no block) `... cites N value(s) no run has recorded yet ...` | nothing to fix: tell the user the experiment is owed |

The last row arrives as context rather than as a blocking error. The hook runs only
after the agent's own edits; after a file is changed by a script, run `vouch lint`
(the same check) on it. `vouch status` ends with when the hook last ran and what it
found.

Inside drawing environments (`tikzpicture`, `axis`, ...) only the text of nodes is
prose, and still checked; coordinates, loops and plotted data are not results.

## Running vouch in a uv project

In a uv project the agent's shell may not have `vouch` on `PATH`; use
`uv run --no-sync vouch ...`, which is also the hook command `vouch init --agents`
writes there. Never edit `.vouch/` or the generated `vouch-values.tex`,
`vouch-tables/` and provenance CSV by hand; `vouch build` writes them.

## A log of vouch in use

Where to improve vouch is learned by using it. In a project that uses it, keep a short
running log, `vouch-dialogue.md`, of what worked and what got in the way, written as it
happens rather than from memory:

```markdown
## 21 October 2026: the revision of section 6

1. **+ `vouch status` shows each run's duration.** It showed that `examples`
   takes 21 minutes, so the re-run went to the background at once.
2. **- One quick claim cost a 21-minute re-run.** ... *Idea:* ...
3. **? The edit hook is silent when all is well.** ...
```

Mark each entry **+** (worked well), **-** (got in the way) or **?** (unclear), with
the concrete symptom and, where one suggests itself, an idea. A clear defect (a false
warning, a misleading message) is worth reporting at once; the rest are worth reviewing
together before a release.

See also: [LLM & agents](index.md), [Claude Code](claude-code.md).
