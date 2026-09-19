# Non-Python experiments

*Summarizes [SPEC.md §14](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

## `vouch run`

```console
$ vouch run cifar_vit_jl --dep src/ --dep configs/vit.yaml --input data/cifar10.npz \
      --out results/vit.csv -- julia train.jl --model vit
```

1. **Before running**, vouch hashes `--dep` paths (semantically for `.py`, by raw content otherwise) and `--input` paths.
2. It runs the command with `VOUCH_RUN`, `VOUCH_ROOT` and `VOUCH_VALUES` (a temporary JSON path) set in the environment.
3. The program writes its values to `$VOUCH_VALUES`, either as full records or as shorthand:

   ```json
   {"values": {"cifar.vit.acc": {"value": 0.912, "fmt": ".1pct", "desc": "ViT top-1", "better": "higher"}},
    "claims": {}, "artifacts": ["results/vit.png"], "tables": {}}
   ```
   ```json
   {"cifar.vit.acc": 0.912}
   ```

4. **On exit 0**, vouch hashes `--out` paths and declared artifacts, and writes a run record with `code.granularity = "deps"`. On a non-zero exit it writes nothing and passes the exit code through.
5. **Python commands.** When the command is `python script.py …`, vouch runs it as `python -m vouch.exec script.py …`. The script gets function-level tracking with no code changes, and its own `vouch.record()`/`@vouch.track` values land in this run.
6. **The recorded command** is the whole `vouch run …` invocation, so the `fix:` line for a stale run re-runs it identically.
7. **Existing results files.** `vouch run … --values results.json` reads values from a file the program already writes, instead of `$VOUCH_VALUES`. It accepts the same shapes as `record_all` (nested JSON, JSONL or CSV, plus `--prefix`, `--row-key` and `--stats`).

## Registering results that already exist: `vouch import`

Results sometimes come from code that has already run: before you adopted
vouch, from a notebook, or from a collaborator's cluster job. `vouch import`
registers the whole file in one command, with honest, reduced provenance:

```console
$ vouch import results/imagenet_eval.json --run imagenet_eval --prefix imagenet \
      --producer experiments/eval_imagenet.py --producer src/models/ \
      --command "python experiments/eval_imagenet.py --split val"
imported 24 values into run imagenet_eval (prefix imagenet) · granularity: declared (3 files)
  metadata from [metrics]: 24/24 described
  note: imported, not recorded live; freshness tracks the declared producer files
```

- **Input shapes:** the same as `record_all`: JSON (nested), JSONL, CSV (`--row-key`), and per-seed lists (`--stats`).
- **Provenance:** the `code.granularity` is `declared`: the `--producer` files and directories are hashed semantically, so editing them makes the run stale. The results file itself is recorded as an input.
- **Honesty in every view:** `trace`, the CSV, and tooltips all say the values were imported. `check` reports `imported` as info, and as a warning when there is no `--producer`.
- Re-running the producer under a normal run replaces the imported record.

See also: [`vouch run`/`vouch import`](../cli/integrations.md).
