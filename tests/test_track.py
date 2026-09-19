"""@vouch.track: results recorded per call, keyed by every argument."""

import enum
import json
import pathlib

import pytest
from conftest import edit

from vouch import cli
from vouch.config import Config
from vouch.freshness import assess
from vouch.store import load_runs
from vouch.track import TrackError, call_key, call_text, segment, track
from vouch.values import decode


# ---------------------------------------------------------------------------
# the key scheme (pure)
# ---------------------------------------------------------------------------

class Opt(enum.Enum):
    ADAM = 1


@pytest.mark.parametrize("name,value,seg", [
    ("dataset", "cifar", "cifar"),
    ("dataset", "CIFAR 10", "CIFAR_10"),
    ("dataset", "", "empty"),
    ("seed", 3, "seed_3"),
    ("lr", 1e-3, "lr_0_001"),
    ("lr", 1e-05, "lr_1e-05"),
    ("t", -0.5, "t_-0_5"),
    ("flag", True, "flag_true"),
    ("cap", None, "cap_none"),
    ("opt", Opt.ADAM, "ADAM"),
    ("layers", [64, 64], "layers_64-64"),
    ("path", pathlib.PurePosixPath("data/x.csv"), "data_x_csv"),
])
def test_segments(name, value, seg):
    assert segment(name, value) == seg


def test_values_that_cannot_go_in_a_key():
    assert segment("data", list(range(10))) is None
    assert segment("cfg", {"a": 1}) is None
    assert segment("model", object()) is None


def test_every_argument_in_signature_order_and_templates():
    key, left = call_key("evaluate", {"dataset": "cifar", "model": "resnet", "seed": 0,
                                      "lr": 0.001, "data": list(range(10))}, None)
    assert key == "evaluate.cifar.resnet.seed_0.lr_0_001" and left == ["data"]
    key, _ = call_key("evaluate", {"dataset": "cifar", "model": "resnet", "lr": 0.01},
                      "{dataset}.{model}.lr{lr}")
    assert key == "cifar.resnet.lr0_01"


def test_long_keys_are_shortened_uniquely():
    a, _ = call_key("f", {f"p{i}": f"value{i}" * 3 for i in range(12)}, None)
    b, _ = call_key("f", {f"p{i}": f"value{i}" * 3 if i else "other" for i in range(12)}, None)
    assert len(a) <= 100 and a != b


def test_decoration_time_errors():
    with pytest.raises(TrackError, match="doesn't take"):
        track(lambda x, seed=0: x, over="seeds")
    with pytest.raises(TrackError, match=r"\['modle'\]"):
        track(key="{modle}")(lambda model: 1)


def test_call_text():
    assert call_text({"function": "exp.py::evaluate", "args": {"dataset": "cifar", "lr": 0.001},
                      "over": {"seed": [0, 1, 2, 3, 4]}}) == \
        "evaluate(dataset=cifar, lr=0.001) over seed=0..4"
    assert call_text({"function": "f", "args": {}, "over": {"seed": [7, 3]}}) == "f() over seed=7, 3"


# ---------------------------------------------------------------------------
# recording, in real runs
# ---------------------------------------------------------------------------

def values(project, run="exp"):
    return project.record(run)["values"]


def test_scalar_dict_and_nested_results(project):
    project.write("exp.py", '''
        import vouch

        @vouch.track
        def throughput(model: str, batch: int = 64):
            return 1200.0

        @vouch.track(desc="metric")
        def metrics(model, split="test"):
            return {"acc": 0.9, "f1": {"macro": 0.8, "micro": 0.85}, "note": None}

        assert throughput("resnet") == 1200.0          # the result passes through untouched
        metrics("vit")
    ''')
    proc = project.run("exp.py", check=True)
    v = values(project)
    assert v["throughput.resnet.batch_64"]["value"] == 1200.0
    assert v["throughput.resnet.batch_64"]["call"] == {
        "function": "exp.py::throughput", "args": {"model": "resnet", "batch": 64},
        "sites": ["exp.py:11"]}
    assert v["throughput.resnet.batch_64"]["site"] == "exp.py:3"          # the definition
    assert set(k for k in v if k.startswith("metrics")) == {
        "metrics.vit.test.acc", "metrics.vit.test.f1.macro", "metrics.vit.test.f1.micro"}
    assert "skipped 1 value(s)" in proc.stderr and "(None)" in proc.stderr


def test_over_seed_combines_into_a_stat(project):
    project.write("exp.py", '''
        import vouch

        @vouch.track(over="seed", desc="acc")
        def evaluate(dataset, model, seed=0, data=None, tag="x"):
            return {"acc": 0.9 + seed / 100, "name": model, "tag": f"{tag}{seed}"}

        for model in ("resnet", "vit"):
            for seed in range(5):
                evaluate("cifar", model, seed=seed, data=list(range(10)))
    ''')
    proc = project.run("exp.py", check=True)
    v = values(project)
    acc = v["evaluate.cifar.resnet.tag_x.acc"] if "evaluate.cifar.resnet.tag_x.acc" in v else \
        v["evaluate.cifar.resnet.x.acc"]
    s = decode(acc["type"], acc["value"])
    assert s.n == 5 and abs(s.mean - 0.92) < 1e-12
    call = acc["call"]
    assert call["over"] == {"seed": [0, 1, 2, 3, 4]} and call["calls"] == 5
    assert call["args"] == {"dataset": "cifar", "model": "resnet", "data": "<list of 10>",
                            "tag": "x"}
    assert call["not_in_key"] == ["data"]
    # a string that is the same on every call is kept; one that differs is not a number
    assert v["evaluate.cifar.resnet.x.name"]["value"] == "resnet"
    assert "evaluate.cifar.resnet.x.tag differs across calls" in proc.stderr.replace(" (", " ")


def test_duplicate_over_values_warn(project):
    project.write("exp.py", '''
        import vouch

        @vouch.track(over="seed")
        def f(seed=0):
            return 1.0

        f(seed=1); f(seed=1)
    ''')
    proc = project.run("exp.py", check=True)
    assert "f() called twice with seed=1" in proc.stderr
    assert decode("stat", values(project)["f"]["value"]).n == 2


def test_methods_kwargs_and_varargs(project):
    project.write("exp.py", '''
        import vouch

        class Trainer:
            @vouch.track(desc="loss")
            def fit(self, epochs, *tags, **opts):
                return 0.5

        Trainer().fit(3, "a", "b", lr=0.1, wd=0.0)
    ''')
    project.run("exp.py", check=True)
    assert list(values(project)) == ["Trainer.fit.epochs_3.tags_a-b.lr_0_1.wd_0_0"]


def test_exceptions_record_nothing_and_propagate(project):
    project.write("exp.py", '''
        import vouch

        @vouch.track(desc="v")
        def f(x):
            if x < 0:
                raise ValueError("negative")
            return float(x)

        f(1)
        try:
            f(-1)
        except ValueError:
            pass
    ''')
    project.run("exp.py", check=True)
    assert list(values(project)) == ["f.x_1"]


def test_none_results_warn_once(project):
    project.write("exp.py", '''
        import vouch

        @vouch.track
        def f(x):
            return None

        for i in range(3):
            f(i)
        vouch.record("other", 1, desc="o")
    ''')
    proc = project.run("exp.py", check=True)
    assert proc.stderr.count("returned None; nothing recorded") == 1


def test_missing_description_warns_once_per_function(project):
    project.write("exp.py", '''
        import vouch

        @vouch.track
        def f(x):
            return float(x)

        for i in range(4):
            f(i)
    ''')
    proc = project.run("exp.py", check=True)
    assert proc.stderr.count("recorded without desc") == 1


def test_async_functions(project):
    project.write("exp.py", '''
        import asyncio
        import vouch

        @vouch.track(desc="v")
        async def f(x):
            return x * 2.0

        assert asyncio.run(f(2)) == 4.0
    ''')
    project.run("exp.py", check=True)
    assert values(project)["f.x_2"]["value"] == 4.0


def test_explicit_run_and_dataframe_results(project):
    pytest.importorskip("pandas")
    project.write("exp.py", '''
        import pandas as pd
        import vouch

        @vouch.track(desc="score")
        def table(split):
            return pd.DataFrame({"acc": [0.9, 0.8]})

        with vouch.run("sweep") as run:
            table("test")
    ''')
    project.run("exp.py", check=True)
    assert set(values(project, "sweep")) == {"table.test.0.acc", "table.test.1.acc"}


def test_editing_the_tracked_function_makes_its_values_stale(project):
    project.write("exp.py", '''
        import vouch

        @vouch.track(desc="v")
        def f(x):
            return float(x)

        f(1)
    ''')
    project.run("exp.py", check=True)
    cfg = Config.load(project.root)
    edit(project.root / "exp.py", "return float(x)", "return float(x) + 1")
    st = assess(cfg, load_runs(cfg.store), check_env=False)["exp"]
    assert st.state == "stale" and st.reasons[0].subject == "exp.py::f"


def test_call_shows_in_trace_tooltip_and_appendix(project, capsys):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n')
    project.write("paper/main.tex", "\\documentclass{article}\n\\usepackage{vouch}\n"
                  "\\begin{document}\\vouch{evaluate.cifar.acc}\\end{document}\n")
    project.write("exp.py", '''
        import vouch

        @vouch.track(over="seed", desc="acc")
        def evaluate(dataset, seed=0):
            return {"acc": 0.9 + seed / 100}

        for s in range(3):
            evaluate("cifar", seed=s)
    ''')
    project.run("exp.py", check=True)
    assert cli.main(["build", "--root", str(project.root)]) == 0
    text = (project.root / "paper/vouch-values.tex").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith(r"\vouch@set{evaluate.cifar.acc}{}"))
    assert "recorded by evaluate(dataset=cifar) over seed=0..2" in line
    prov = next(ln for ln in text.splitlines() if ln.startswith(r"\vouch@prov{evaluate.cifar.acc}"))
    assert "recorded by evaluate(dataset=cifar) over seed=0..2" in prov
    capsys.readouterr()
    assert cli.main(["trace", "--root", str(project.root), "evaluate.cifar.acc.mean"]) == 0
    out = capsys.readouterr().out
    assert "by        evaluate(dataset=cifar) over seed=0..2" in out
    assert "called at exp.py:8" in out
