# Claude Code

*Summarizes [SPEC.md §13.5](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

```console
$ vouch init --agents --yes
```

Each of the three parts below is optional, and the command shows a diff before
writing anything.

## 1. Skill: `.claude/skills/vouch/SKILL.md`

Loads only when relevant, so it costs no context otherwise. Its description
triggers on editing `.tex` in a vouch project, writing experiment code that
produces results, or mentions of results, numbers, tables or claims. It has
four workflows:

- *Record results in an experiment* — see [While writing experiment code](index.md#while-writing-experiment-code): a docstring on the function, a run per configuration, checked properties as claims with their evidence, tables whose cells are keys
- *Write a results paragraph*: search → cite (its `context:` line is the producing function's own docstring, if it has one; `[w]`, `[,d]`, `[.0pct]`, `[.2fx]` for prose) → compare or define (a definition may read a file or a table) → claim → check; a definition is added before the sentence that cites it
- *Handle changed values*: `vouch changes` → re-read each sentence → fix the text → report suspicious changes → ask before acking; a redefined experiment gets new key names
- *Convert an existing paper*: `suggest` → `--apply` → resolve `no-source` with the user

[Patterns for agents](patterns.md) is the long form, with examples.

It ends with a command cheat-sheet, including `vouch document` — see [CLI reference](../cli/inspect.md#vouch-document).

## 2. Rules block

Appended to `CLAUDE.md` (or `AGENTS.md`), between `<!-- vouch -->` markers so it
can be updated in place:

```markdown
<!-- vouch -->
## Numbers in the paper (vouch)
- Never type an empirical number into LaTeX. Find it (`vouch search`, `.vouch/CATALOG.md`), then paste what `vouch cite KEY` prints.
- If it doesn't exist: record it in the experiment, `@vouch.derive` it, or `vouch.expect()` it and tell the user it is owed.
- Give a new tracked function a real docstring -- vouch shows it back at lookup time (`vouch cite`, `vouch search`) as a sanity check. If one already exists on a function you're writing about, use it instead of guessing a description.
- Never compute with numbers in prose (differences, ratios, "2x"): `vouch compare A B --write`, then cite the derived key.
- Qualitative comparisons ("outperforms", "all seeds") go in `\vouchclaim` backed by a claim.
- Before finishing: `vouch build` and `vouch check` must pass. Before submitting: `vouch check --strict`, which also fails on pending `vouch.expect()` values -- allowed while results are owed, never replaced by invented numbers.
- If `vouch changes` lists anything: re-read each cited sentence, fix wrong text, and report SUSPICIOUS changes to the user.
- Never run `vouch ack` or `vouch accept` without the user's approval. Never edit `.vouch/` or generated files.
<!-- /vouch -->
```

## 3. PostToolUse hook

In `.claude/settings.json`:

```json
{"hooks": {"PostToolUse": [{"matcher": "Edit|Write|MultiEdit",
  "hooks": [{"type": "command", "command": "vouch hook claude"}]}]}}
```

The agent runs the hook from its own shell, not from the environment `vouch init`
ran in, so `init` writes the command that shell can reach:

| the project | the command |
|---|---|
| a uv project whose `uv.lock` installs `vouch-paper` | `uv run --no-sync vouch hook claude` |
| vouch in a virtual environment inside the project | `"$CLAUDE_PROJECT_DIR"/.venv/…/vouch hook claude` |
| vouch on `PATH`, outside any virtual environment | `vouch hook claude` |
| anything else | this Python's absolute path, in `.claude/settings.local.json` |

`vouch hook claude` reads the hook payload from stdin and ignores anything that
isn't a `.tex` file in a configured paper. For those, it checks just the edited
file for:

- unknown keys, with did-you-mean
- malformed macros (`\vouch{` never closed, `\vouchclaim` without its prose)
- typed numbers: the exact `\vouch[fmt]{key}` to replace each with, or "matches no recorded value"

If it finds problems, it prints them with their fixes to stderr and exits `2`,
which feeds them back to the agent in the same turn, so a mistake is corrected
immediately instead of at commit time. Pending keys are not reported here:
citing one is the approved move.

```
vouch: paper/results.tex has 3 problem(s) -- fix them now:
  line 1: 93.2% is typed by hand; it is cifar.resnet.acc.mean: replace it with \vouch[.1pct]{cifar.resnet.acc.mean}
  line 1: unknown key cifar.resnt.acc: no run, definition or vouch.expect provides it (did you mean cifar.resnet.acc?)
  line 2: \vouchclaim takes two arguments: \vouchclaim{key}{the prose it vouches for}
```

It's stdlib-only and reads a precomputed index (`.vouch/cache/index.json`,
written by `build`), so a tex edit costs on the order of 200–300 ms including
interpreter startup. A run you record after the last build counts at once: the
hook also reads the run records written since the index, so their keys are known
and their numbers are matched without another `vouch build`.

## `vouch init --agents [skill,rules,hook] [--yes] [--stop-gate]`

- Prints a unified diff of every file it would write. Without `--yes` it writes only after a yes at a terminal, and never when run non-interactively.
- The rules block replaces an existing `<!-- vouch -->` block in place.
- The hook merges into existing settings and never duplicates itself.
- `--stop-gate` also installs a `Stop` hook, `vouch hook stop`. It runs `vouch check --strict` and exits 2 with the issues, so an agent can't finish while the gate fails. It is off by default, because blocking an agent from finishing is intrusive.

See also: [CLI reference — `vouch init`](../cli/setup.md#vouch-init), [Patterns for agents](patterns.md), [MCP server](mcp-server.md).
