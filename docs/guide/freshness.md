# Freshness

*Summarizes [SPEC.md §8](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

A run is **fresh** when the code it executed and the inputs it read are
unchanged since it recorded. Freshness is computed from content hashes. Git is
not required; commits are recorded only as metadata.

## Semantic hashing

A code unit's hash is SHA-256 (truncated to 16 hex characters) of the unit's AST
after stripping docstrings. As a result:

- **Comments, docstrings, blank lines, formatting and moving code around never change a hash.** Such edits are *cosmetic*, reported as `info`, and never fail.
- Any change to logic changes the hash: code, defaults, decorators, annotations and constants. Renaming a local variable counts as a change — rare, and when it happens you record the judgement with `vouch accept`.

**Units per file:**

| Unit | Contents |
|---|---|
| `path::<module>` | module-level statements other than definitions |
| `path::Class` | the class header and class-level statements other than methods and nested classes |
| `path::func`, `path::Class.method` | the whole definition: decorators, signature, defaults, body, including nested functions |

Definitions are left out of the enclosing unit, so reordering functions never
changes a hash, and neither does editing one a run never executed.

## Function-level tracking (Python ≥ 3.12)

On `import vouch`, vouch registers a `sys.monitoring` (PEP 669) `PY_START`
callback. Its cost is one call per distinct function, however hot the run's
loops are — measured worst case, a tight pure-Python loop: 1–2%, within
run-to-run noise. For code that spends its time in numpy or torch, the cost is
zero.

It records executed units (mapping each code object's `co_qualname` to its
unit) and source snapshots (each file's text as it was when the run first
loaded it).

Consequences:

- **Editing a function the run never executed doesn't make it stale.** The run is reported as `cosmetic`, which passes.
- The staleness report names the unit: `src/models.py::ResNet.forward changed`.

**Where precision isn't safe, files count whole**, recorded under
`code.whole_files`, when: a module was imported before `import vouch`; the
entry script had code that could have called first-party code before
`import vouch`; vouch was imported inside a function; or executed code can't be
found in the source.

**The whole run falls back to module granularity** when: the Python version has
no `sys.monitoring` (< 3.12); no monitoring tool id is free; `VOUCH_TRACE=0` is
set; `[freshness] granularity = "module"` is configured; or the run started
Python child processes, which aren't observed.

`vouch trace KEY` shows how each file was tracked (`tracked by function`, or
`tracked whole -- <reason>`).

## Inputs, artifacts and upstream staleness

- **Inputs** are hashed by content, or by size+mtime with `mode="stat"`. A hash cache in `.vouch/cache/hashes.json` makes repeated checks cheap.
- **Dependency between runs.** If run *B* recorded an input that is an artifact of run *A*, then *B* depends on *A*. If *A*'s recorded artifact hash differs from what *B* recorded as its input, *B* is **stale**. If *A* is stale, *B* is **upstream-stale**.
- **Artifacts on disk** are checked when present. A mismatch is `tampered`. A missing file is `absent` (only a warning — the committed store alone is enough to check the paper).

## Run states

| State | Meaning | Default severity |
|---|---|---|
| `fresh` | every unit, input and artifact matches | ok |
| `cosmetic` | files changed, but not in any unit the run depends on | info |
| `stale` | a unit's semantic hash changed, a unit disappeared, or an input changed | **error** |
| `upstream-stale` | a run this one read from is stale | **error** |
| `tampered` | an artifact on disk differs from its recorded hash | **error** |
| `incomplete` | the record's `status` is not `complete` | **error** |
| `accepted` | stale, but the exact current state was reviewed | info |
| `env-drift` | a recorded third-party package version differs from the current environment | warning |
| `absent` | a recorded input or artifact is not on disk | warning |

## Accepting a reviewed staleness

Not every logic change changes a result: a refactor, an added log line, a
renamed variable. That judgement belongs to a person, and it is recorded:

```console
$ vouch accept cifar_resnet --why "added a progress bar to ResNet.forward; outputs unchanged"
```

`.vouch/accepted.toml` then records the run, the exact current hashes of the
changed units and inputs, who, when and why. The acceptance clears the run only
for those hashes: the next edit re-opens it.

Accepting is a human action. The Claude Code integration's rules forbid an
agent from doing it without the user's approval — see [Claude Code](../llm/claude-code.md).
