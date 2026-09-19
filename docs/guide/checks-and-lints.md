# Checks and lints

*Summarizes [SPEC.md §11](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

`vouch check` runs every check below. It never executes experiments or user
code.

- **Exit codes:** `0` pass, `1` fail, `2` could not check (unreadable config, unparseable tex, no store).
- **Severities:** `error` always fails. `warning` fails under `--strict`. `info` never fails. Override per check with `[check] severity`.

| Check | Default | Trigger | Fix |
|---|---|---|---|
| `config` | error | invalid `vouch.toml` or tex graph | message names the file and line |
| `store-edited` | error | a run record's or `derived.json`'s `record_hash` doesn't match its content | re-run the experiment (or `vouch build`); never hand-edit `.vouch/` |
| `derive-error` | error | a values module failed to import, or a definition raised, read an unknown key, read a failed definition, returned the wrong type, or was defined twice | fix the definition, then `vouch build` |
| `unknown-key` | error | a cited key exists nowhere (did-you-mean suggestions included) | fix the key, or `record`/`derive`/`expect` it |
| `key-conflict` | error | two sources produce one key | rename one |
| `out-of-sync` | error | generated files or `derived.json` differ from what `build` would write | `vouch build` |
| `stale` / `upstream-stale` | error (info when the paper cites nothing from the run) | see [Freshness](freshness.md) | re-run (exact command shown) or `vouch accept` |
| `tampered` / `incomplete` | error | see [Freshness](freshness.md) | re-run |
| `untracked-input` | error | a derivation reads a file no run produced and not listed as `external` | produce it in a run, or declare it `external` |
| `alias-target` | warning | an alias names a key under which nothing is recorded | fix the alias |
| `derive-cycle` | error | derived values depend on each other | break the cycle |
| `false-claim` | error | a claim no longer holds; the message shows the values it read | re-examine the result and the prose |
| `figure-stale` | error | a cited figure's producing run is stale | re-run |
| `changed` / `suspicious` / `figure-changed` | warning | see [Change notification](change-notification.md) | re-read the cited sentences, then `vouch ack` |
| `fragile` | warning | a claim holds by less than `changes.claim_margin` | soften the claim, or check it is not noise |
| `pending` | warning | a cited `expect()` key has no producing run | run the `producer` command |
| `no-source` | warning | a bare number in prose that matches **no** recorded value: the signature of an invented number | find the real value (`vouch search`) or remove the number |
| `bare-number` | warning | a bare number that **does** match a recorded value | `vouch suggest --apply` |
| `figure-untracked` | warning | an `\includegraphics` with no producing run | save it inside a run |
| `no-description` | warning | a cited key has no `desc` | add `desc=` |
| `env-drift` / `absent` | warning | see [Freshness](freshness.md) | re-run if the difference matters |
| `imported` | info (warning without `--producer`) | a run was registered with `vouch import`, not recorded live | re-run the producer under vouch for full provenance |
| `cosmetic`, `accepted`, `hidden`, `reformatted`, `unused-value`, `expectation-met`, `default-float-format`, `dirty-tree-at-record` | info | — | — |

## The bare-number lint

- **Scope:** the document body, with comments, vouch macro arguments and LaTeX plumbing masked. Plumbing includes `\label`, the `\ref` family, `\cite*`, URLs, `\includegraphics` options, lengths and units, tabular column specs, and `\begin`/`\end`.
- **Always flagged**, including in math: decimals (`0.93`), percentages (`12.5\%`), `\times` multipliers, thousands separators (`18{,}535`), scientific notation, and `\pm` pairs.
- **Flagged in text mode:** integers of two or more digits (`38 layers`). Years (`19xx`/`20xx`) are skipped when `allow_years` is on. Single digits and spelled-out numbers are never flagged.
- **Exemptions:** `[lint] allow` rules (a regex matched against context, plus a reason), and a `% vouch: ignore` pragma on the line.
- **Matching.** Each flagged literal is parsed together with its implied precision (`93.2\%` claims 1 decimal place), then compared against every recorded value and its common transforms. A match is exact when rounding the candidate to the literal's implied precision reproduces the literal.
- **Messages** name the key and the exact snippet that prints the literal:
  ```
  ! bare-number    93.2% is typed by hand; it is cifar.resnet.acc.mean: cite it as \vouch[.1pct]{cifar.resnet.acc.mean}
  ! no-source      93.4% matches no recorded value; the closest recorded value is cifar.resnet.acc.max = 93.3, which doesn't round to it: record it where it is computed, or remove it
  ```
- **Where it runs.** `vouch build` and `vouch check` both report it, per paper. `[lint] level` is `"warn"` (default), `"error"` or `"off"`. `--strict` makes the warnings errors.

See also: [`vouch suggest`](../cli/author.md), [`vouch check`](../cli/check.md).
