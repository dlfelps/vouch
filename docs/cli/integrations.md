# Integrations: run, import, mcp

## `vouch run`

Record a run of any command — the entry point for non-Python experiments.

```console
$ vouch run ID [--dep PATH]... [--input PATH]... [--out PATH]... [--values FILE] \
      [--prefix P] [--row-key C] [--stats] -- CMD...
```

| Flag | Meaning |
|---|---|
| `ID` | the run id |
| `--dep PATH` | code the run depends on (file or directory); repeatable |
| `--input PATH` | data the run reads; repeatable |
| `--out PATH` | a file the run writes (an artifact); repeatable |
| `--values FILE` | read values from this results file instead of `$VOUCH_VALUES` |
| `--prefix` | prefix for every key |
| `--row-key` | for tabular values: the column naming each row |
| `--stats` | lists of numbers become Stats |
| `-- CMD...` | everything after `--` is the command to run |

```console
$ vouch run cifar_vit_jl --dep src/ --dep configs/vit.yaml --input data/cifar10.npz \
      --out results/vit.csv -- julia train.jl --model vit
```

Full details: [Non-Python experiments](../guide/non-python-experiments.md).

## `vouch import`

Register an existing results file as a run, with honest, reduced provenance.

```console
$ vouch import FILE --run ID [--prefix P] [--producer PATH]... [--command TEXT] \
      [--row-key C] [--stats] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `FILE` | the results file |
| `--run ID` | the run id (required) |
| `--prefix` | prefix for every key |
| `--producer PATH` | code that produced the file (file or dir); repeatable |
| `--command TEXT` | the command that produced it (declared, not observed) |
| `--row-key` | for tabular files: the column naming each row |
| `--stats` | lists of numbers become Stats |

```console
$ vouch import results/imagenet_eval.json --run imagenet_eval --prefix imagenet \
      --producer experiments/eval_imagenet.py --producer src/models/ \
      --command "python experiments/eval_imagenet.py --split val"
```

## `vouch mcp`

Serve `search`/`cite`/`compare`/`check`/… to any MCP client over stdio.

```console
$ vouch mcp
```

Read-only except that `compare` may write a definition when asked. Built on
newline-delimited JSON-RPC 2.0 with the standard library — no extra
dependencies. `vouch init --agents mcp` registers it in `.mcp.json`.

Full tool list and protocol details: [MCP server](../llm/mcp-server.md).
