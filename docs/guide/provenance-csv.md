# Provenance CSV

*Summarizes [SPEC.md §10](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

The everyday way to see where a number came from is the PDF's provenance
appendix (see [LaTeX interface](latex.md)). The CSV is the same information as a
table, for when there is no draft PDF: a final (double-blind) submission's
supplementary material, artifact evaluation, a spreadsheet, or a script.

`vouch export --csv PATH` writes it on demand. Setting `provenance_csv` under
`[[paper]]` makes every build write it too; it is then committed and checked
for `out-of-sync` like the other generated files.

It has **one row per key the paper cites**, in reading order. Figures cited
through `\includegraphics` get rows too. It is deterministic, so a committed
copy's diff is readable.

| Column | Content |
|---|---|
| `key` | the cited key |
| `kind` | `value` · `claim` · `derived` · `param` · `table-cell` · `table` · `figure` · `pending` |
| `rendered` | every rendered form cited, joined with ` \| ` |
| `raw_value` | the raw value (a JSON number, or JSON for Stat and tuple); for a claim, `true`/`false` plus the values it read |
| `fmt`, `unit`, `description`, `better` | as recorded |
| `experiment` | the run id, or `derive:vouch_values.py::<function>` for derived values |
| `script` | the run's entry script, or the values module |
| `call_site` | file:line of the `record()` call or the definition |
| `command` | the exact command that produced the run |
| `params` | the run's parameters, as JSON |
| `inputs` | input paths with short hashes; for derived values, the keys read |
| `git_commit`, `git_dirty` | at record time |
| `recorded_at`, `duration_s` | from the run |
| `freshness` | a freshness state (see [Freshness](freshness.md)) |
| `change_status` | `acked` · `changed` · `suspicious` · `hidden` · `new` |
| `previous_value`, `acked_at` | from the baseline |
| `cited_at` | every tex file:line, joined with `;` |

`vouch export --csv PATH [--all] [--json]` writes the same table on demand.
`--all` also includes keys that are recorded but not cited. There is exactly
one CSV per paper, covering every cited key — no per-key files.
