# 7. Submitting, and where next

<!-- kept in sync manually with examples/tutorial/README.md; update both -->

## Submitting

`\usepackage[final]{vouch}` removes the links and the provenance appendix, and
leaves plain numbers. `vouch check --strict` still verifies everything, so keep
it in CI.

## Where next

- **Numbers you don't have yet.** `vouch.expect("key", desc=..., producer="python ...")`
  in `vouch_values.py` lets you cite a result before the experiment exists. The
  PDF shows `[pending: key]` and `vouch todo` lists what's owed. See
  [Derived values, claims, tables](../guide/derived-values.md).
- **Comparisons.** `vouch compare evaluate.knn.n_train_640.acc evaluate.linear.n_train_640.acc`
  prints the difference, a Welch t-test and the `@vouch.derive` and `@vouch.claim`
  that make it citable. `--write` adds them to `vouch_values.py`. See
  [`vouch compare`](../cli/author.md).
- **No code changes at all.** List the function in `vouch.toml`
  (`[[track]] function = "experiment.py::evaluate"`) and run
  `python -m vouch.exec experiment.py`. See
  [Recording (Python API)](../guide/recording.md#tracking-without-touching-the-code).
- **Writing with an LLM agent.** `vouch init --agents` installs a Claude Code skill
  and a hook. The hook catches a typed number or a mistyped key the moment the
  agent writes it. See [Claude Code](../llm/claude-code.md).
- The full design is in [SPEC.md](https://github.com/dlfelps/vouch/blob/main/SPEC.md)
  in the repository.

You've now covered the whole loop: record, find, cite, derive, check, and
respond to a change. From here, the [Guide](../guide/concepts.md) and
[CLI reference](../cli/index.md) sections go deeper on each part.
