"""Recording, end to end: real scripts, real interpreter exits."""

import json

import pytest

from vouch import store
from vouch.values import decode


def value(rec, key):
    v = rec["values"][key]
    return decode(v["type"], v["value"])


# ---------------------------------------------------------------------------
# the implicit run
# ---------------------------------------------------------------------------

def test_implicit_run_records_everything(project):
    project.write("src/models.py", """
        def score(x):
            return 0.9 + x

        def unused():
            return 1
    """)
    project.write("experiments/train.py", """
        import sys
        sys.path.insert(0, "src")
        import vouch
        from models import score

        vouch.params({"model": "resnet", "optim": {"lr": 0.1}})
        acc = vouch.record("cifar.resnet.acc", score(0.0321), fmt=".1%",
                           desc="top-1 test accuracy", better="higher")
        assert acc == 0.9 + 0.0321
    """)
    proc = project.run("experiments/train.py", check=True)
    assert "recorded run experiments.train: 1 value(s)" in proc.stderr

    rec = project.record("experiments.train")
    assert store.verify_record(rec)
    assert rec["entry"] == "experiments/train.py"
    assert rec["command"] == ["python", "experiments/train.py"]
    assert rec["params"] == {"model": "resnet", "optim.lr": 0.1}
    v = rec["values"]["cifar.resnet.acc"]
    assert v == {"type": "float", "value": 0.9321, "fmt": ".1pct", "better": "higher",
                 "desc": "top-1 test accuracy", "site": "experiments/train.py:7"}
    units = rec["code"]["units"]
    assert rec["code"]["granularity"] == "function"
    assert {"src/models.py::score", "src/models.py::<module>",
            "experiments/train.py::<module>"} <= set(units)
    assert "src/models.py::unused" not in units          # never executed
    assert set(rec["code"]["files"]) == {"src/models.py", "experiments/train.py"}
    assert rec["env"]["python"].count(".") == 2


def test_uncaught_exception_writes_nothing_and_keeps_previous(project):
    project.write("exp.py", """
        import sys, vouch
        vouch.record("x", float(sys.argv[1]), desc="x")
        if sys.argv[2] == "crash":
            raise RuntimeError("boom")
    """)
    project.run("exp.py", "1.0", "ok", check=True)
    proc = project.run("exp.py", "2.0", "crash")
    assert proc.returncode != 0
    assert "run exp discarded (the script raised an uncaught exception)" in proc.stderr
    assert value(project.record("exp"), "x") == 1.0


def test_keyboard_interrupt_writes_nothing(project):
    project.write("exp.py", """
        import vouch
        vouch.record("x", 1.0, desc="x")
        raise KeyboardInterrupt
    """)
    project.run("exp.py")
    assert not project.has_record("exp")


# ---------------------------------------------------------------------------
# explicit runs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ending,written", [
    ("pass", True),
    ("raise SystemExit(0)", True),
    ("raise SystemExit(1)", False),
    ("raise ValueError('bad')", False),
])
def test_explicit_run_atomicity(project, ending, written):
    project.write("exp.py", f"""
        import vouch
        with vouch.run("r1") as run:
            run.record("x", 1.0, desc="x")
            {ending}
    """)
    proc = project.run("exp.py")
    assert project.has_record("r1") is written
    if not written:
        assert "run r1 discarded" in proc.stderr


def test_explicit_run_prefix_params_inputs_artifacts(project):
    project.write("data/train.csv", "a,b\n1,2\n")
    project.write("exp.py", """
        import argparse, vouch
        args = argparse.Namespace(model="vit", epochs=100)
        with vouch.run("cifar_vit", params=args, prefix="cifar.vit") as run:
            run.input("data/train.csv")
            run.artifact("results/out.csv")          # declared before it exists
            run.artifact("figs/missing.pdf")
            vouch.record("acc", 0.9, desc="acc")     # module-level call -> active run
            import os
            os.makedirs("results", exist_ok=True)
            open("results/out.csv", "w").write("x\\n1\\n")
    """)
    proc = project.run("exp.py", check=True)
    rec = project.record("cifar_vit")
    assert list(rec["values"]) == ["cifar.vit.acc"]
    assert rec["params"] == {"epochs": 100, "model": "vit"}
    assert rec["inputs"]["data/train.csv"].startswith("sha256:")
    art = rec["artifacts"]
    assert art["results/out.csv"]["hash"].startswith("sha256:")
    assert art["results/out.csv"]["kind"] == "data"
    assert art["figs/missing.pdf"] == {"hash": None, "kind": "figure", "site": "exp.py:6"}
    assert "artifact figs/missing.pdf was declared but does not exist" in proc.stderr


def test_runs_cannot_nest(project):
    project.write("exp.py", """
        import vouch
        with vouch.run("a"):
            with vouch.run("b"):
                pass
    """)
    proc = project.run("exp.py")
    assert "vouch runs cannot nest" in proc.stderr
    assert not project.has_record("a") and not project.has_record("b")


def test_sweep_loop_records_one_run_per_config(project):
    project.write("sweep.py", """
        import vouch
        for lr in (0.1, 0.01):
            with vouch.run(f"sweep_lr{str(lr).replace('.', 'p')}", params={"lr": lr}) as run:
                run.record(f"sweep.lr{str(lr).replace('.', 'p')}.acc", 1 - lr, desc="acc")
    """)
    project.run("sweep.py", check=True)
    assert project.record("sweep_lr0p1")["params"] == {"lr": 0.1}
    assert project.has_record("sweep_lr0p01")


# ---------------------------------------------------------------------------
# validation: warnings phrased as fixes, never errors
# ---------------------------------------------------------------------------

def test_validation_warnings(project):
    project.write("exp.py", """
        import vouch
        vouch.record("CIFAR acc", 0.9, desc="acc")
        vouch.record("nodesc", 1.0)
        vouch.record("loss", float("nan"), desc="loss")
        vouch.record("name", "ResNet", better="higher", desc="model")
        vouch.record("bad_fmt", 1.0, fmt=".1q", desc="x")
        vouch.record("dup", 1.0, desc="x")
        vouch.record("dup", 2.0, desc="x")
        vouch.record("obj", object(), desc="x")
    """)
    proc = project.run("exp.py", check=True)
    err = proc.stderr
    assert "'CIFAR acc' is not a valid key; use dots/underscores, e.g. 'CIFAR_acc'" in err
    assert 'nodesc has no desc; add desc="..."' in err
    assert "loss is nan; the run will record it, but check will flag it" in err
    assert "name: better= only applies to numeric values" in err
    assert "bad_fmt: not a vouch format" in err
    assert "dup recorded twice in run exp (exp.py:7, then exp.py:8); the last value wins" in err
    assert "cannot record obj: object is not a number" in err
    rec = project.record("exp")
    assert set(rec["values"]) == {"CIFAR_acc", "nodesc", "loss", "name", "bad_fmt", "dup"}
    assert value(rec, "dup") == 2.0
    assert "better" not in rec["values"]["name"] and "fmt" not in rec["values"]["bad_fmt"]


def test_strict_mode_rejects_bad_keys(project):
    project.write("exp.py", """
        import vouch
        vouch.record("CIFAR acc", 0.9)
    """)
    proc = project.run("exp.py", env={"VOUCH_STRICT": "1"})
    assert proc.returncode != 0 and "is not a valid key" in proc.stderr


def test_no_config_still_records(tmp_path):
    from conftest import Project
    root = tmp_path / "bare"
    root.mkdir()
    p = Project(root)
    p.write("exp.py", "import vouch\nvouch.record('x', 1, desc='x')\n")
    proc = p.run("exp.py", check=True)
    assert "no vouch.toml found; recording into" in proc.stderr
    assert p.has_record("exp")


# ---------------------------------------------------------------------------
# record_all
# ---------------------------------------------------------------------------

def test_record_all_nested_dict_with_metrics_defaults(project):
    project.write("vouch.toml", """
        [metrics]
        "*.acc"  = { fmt = ".1%", better = "higher", desc = "top-1 accuracy, {1} on {0}" }
        "*.loss" = { fmt = ".3f", better = "lower",  desc = "test loss, {1} on {0}" }
        "*.f1.*" = { fmt = ".3f", better = "higher", desc = "{-1} F1, {1} on {0}" }
    """)
    project.write("exp.py", """
        import vouch
        metrics = {"acc": 0.93, "loss": 0.21, "f1": {"macro": 0.90, "micro": 0.93},
                   "eval/runtime": 12.5, "epoch": 3}
        out = vouch.record_all(metrics, prefix="cifar.resnet", exclude=["*.runtime", "*.epoch"])
        assert out is metrics
    """)
    proc = project.run("exp.py", check=True)
    rec = project.record("exp")
    assert sorted(rec["values"]) == ["cifar.resnet.acc", "cifar.resnet.f1.macro",
                                     "cifar.resnet.f1.micro", "cifar.resnet.loss"]
    # fully described by [metrics]: no desc warning at all
    assert "without desc" not in proc.stderr
    # [metrics] is applied when reading, not frozen into the record
    assert "fmt" not in rec["values"]["cifar.resnet.acc"]
    assert all(v["site"] == "exp.py:4" for v in rec["values"].values())


def test_record_all_arguments_globs_and_one_line_summaries(project):
    project.write("exp.py", """
        import vouch
        class T:                                   # a one-element tensor stand-in
            def size(self): return (1,)
            def numel(self): return 1
            def item(self): return 0.5
        metrics = {"eval/acc": 0.9, "eval/loss": T(), "train/acc": 0.95,
                   "confusion": [[1, 2], [3, 4]], "note": None, "a b": 1.0, "c d": 2.0}
        vouch.record_all(metrics, prefix="m",
                         fmt={"*.acc": ".1%", "m.eval.acc": ".2%"},
                         desc={"*.acc": "accuracy", "*.loss": "loss"})
    """)
    proc = project.run("exp.py", check=True)
    rec = project.record("exp")
    vals = rec["values"]
    assert vals["m.eval.acc"]["fmt"] == ".2pct"          # exact key beats glob
    assert vals["m.train.acc"]["fmt"] == ".1pct"
    assert vals["m.eval.loss"]["value"] == 0.5 and vals["m.eval.loss"]["desc"] == "loss"
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("vouch: ")]
    renamed = [ln for ln in lines if "renamed" in ln]
    skipped = [ln for ln in lines if "skipped" in ln]
    nodesc = [ln for ln in lines if "without desc" in ln]
    assert len(renamed) == 1 and "eval/acc->eval.acc" in renamed[0].replace("→", "->")
    assert len(skipped) == 1 and "m.confusion (list of 2)" in skipped[0] and "m.note (None)" in skipped[0]
    assert len(nodesc) == 1 and "2 key(s) recorded without desc" in nodesc[0]


def test_record_all_stats_and_ranges(project):
    project.write("exp.py", """
        import vouch
        vouch.record_all({"acc": [0.93, 0.935, 0.931], "rng": [1, 2]}, prefix="p",
                         stats=True, desc="x", include=["*.acc"])
        vouch.record_all({"rng": [41, 53]}, prefix="q", desc="range")
    """)
    project.run("exp.py", check=True)
    rec = project.record("exp")
    assert set(rec["values"]) == {"p.acc", "q.rng"}
    s = value(rec, "p.acc")
    assert s.n == 3 and abs(s.mean - 0.932) < 1e-12
    assert value(rec, "q.rng") == (41, 53)


def test_record_all_rows_with_table_and_results_file(project):
    project.write("results/sweep.csv", "lr,acc,loss\n0.1,0.90,0.30\n0.01,0.93,0.21\n")
    project.write("exp.py", """
        import vouch
        vouch.record_all("results/sweep.csv", prefix="sweep", row_key="lr",
                         fmt={"*.acc": ".1%"}, desc="sweep metric", table="lr_sweep")
    """)
    project.run("exp.py", check=True)
    rec = project.record("exp")
    # a row name like 0.1 is one key segment, not two
    assert set(rec["values"]) == {"sweep.0_1.acc", "sweep.0_1.loss", "sweep.0_01.acc",
                                  "sweep.0_01.loss"}
    assert rec["inputs"]["results/sweep.csv"].startswith("sha256:")
    t = rec["tables"]["lr_sweep"]
    assert t["columns"] == ["lr", "acc", "loss"] and t["row_key"] == "lr"
    assert t["rows"] == [[0.1, 0.9, 0.3], [0.01, 0.93, 0.21]]
    assert t["fmt"] == {"acc": ".1pct"}


def test_record_all_rejects_non_tabular(project):
    project.write("exp.py", """
        import vouch
        vouch.record_all(42)
    """)
    proc = project.run("exp.py", check=True)
    assert "record_all needs a dict, dataclass, DataFrame" in proc.stderr


def test_pandas_dataframe_if_available(project):
    pytest.importorskip("pandas")
    project.write("exp.py", """
        import pandas as pd, vouch
        df = pd.DataFrame({"model": ["resnet", "vit"], "acc": [0.93, 0.91]})
        vouch.record_all(df, prefix="cifar", row_key="model", desc="acc", table="main")
    """)
    project.run("exp.py", check=True)
    rec = project.record("exp")
    assert set(rec["values"]) == {"cifar.resnet.acc", "cifar.vit.acc"}
    assert rec["tables"]["main"]["rows"] == [["resnet", 0.93], ["vit", 0.91]]


# ---------------------------------------------------------------------------
# claims and tables
# ---------------------------------------------------------------------------

def test_claims_and_tables(project):
    project.write("exp.py", """
        import vouch
        ok = vouch.claim("cifar.no_div", True, desc="no seed diverged", values={"n": 5})
        assert ok is True
        vouch.table("ablation", [{"variant": "none", "acc": 0.91},
                                 {"variant": "mixup", "acc": vouch.Stat.of([0.93, 0.94])}],
                    row_key="variant", fmt={"acc": ".1%"}, highlight={"acc": "max"},
                    second="underline", midrules=[1], desc="augmentations")
    """)
    project.run("exp.py", check=True)
    rec = project.record("exp")
    assert rec["claims"]["cifar.no_div"] == {"holds": True, "desc": "no seed diverged",
                                             "values": {"n": 5}, "site": "exp.py:2"}
    t = rec["tables"]["ablation"]
    assert t["rows"][1][1]["$stat"]["n"] == 2
    assert t["highlight"] == {"acc": "max"} and t["midrules"] == [1]


# ---------------------------------------------------------------------------
# determinism across re-runs
# ---------------------------------------------------------------------------

def test_rerun_is_identical_except_timing(project):
    project.write("exp.py", """
        import vouch
        vouch.record_all({"b": 2, "a": 1.5}, prefix="k", desc="x")
    """)
    project.run("exp.py", check=True)
    first = project.record("exp")
    project.run("exp.py", check=True)
    second = project.record("exp")
    for rec in (first, second):
        for k in ("started", "duration_s", "record_hash"):
            rec.pop(k)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
