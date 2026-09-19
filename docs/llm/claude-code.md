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

- *Record results in an experiment* — see [While writing experiment code](index.md#while-writing-experiment-code)
- *Write a results paragraph*: search → cite → compare → claim → check
- *Handle changed values*: `vouch changes` → re-read each sentence → fix the text → report suspicious changes → ask before acking
- *Convert an existing paper*: `suggest` → `--apply` → resolve `no-source` with the user

It ends with a command cheat-sheet.

## 2. Rules block

Appended to `CLAUDE.md` (or `AGENTS.md`), between `<!-- vouch -->` markers so it
can be updated in place:

```markdown
<!-- vouch -->
## Numbers in the paper (vouch)
- Never type an empirical number into LaTeX. Find it (`vouch search`, `.vouch/CATALOG.md`), then paste `vouch cite KEY`.
- If it doesn't exist: `record()` it in the experiment, `@vouch.derive` it, or `vouch.expect()` it and tell the user.
- Never compute with numbers in prose (differences, ratios, "2x"): `vouch compare A B --write`, then cite the derived key.
- Qualitative comparisons ("outperforms", "all seeds") go in `\vouchclaim` backed by a claim.
- Before finishing: `vouch check --strict` must pass.
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
interpreter startup.

## `vouch init --agents [skill,rules,hook] [--yes] [--stop-gate]`

- Prints a unified diff of every file it would write. Without `--yes` it writes only after a yes at a terminal, and never when run non-interactively.
- The rules block replaces an existing `<!-- vouch -->` block in place.
- The hook merges into existing settings and never duplicates itself.
- `--stop-gate` also installs a `Stop` hook, `vouch hook stop`. It runs `vouch check --strict` and exits 2 with the issues, so an agent can't finish while the gate fails. It is off by default, because blocking an agent from finishing is intrusive.

See also: [CLI reference — `vouch init`](../cli/setup.md#vouch-init), [MCP server](mcp-server.md).
