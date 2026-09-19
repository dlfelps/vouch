"""[[track]] in vouch.toml: results recorded without a decorator (SPEC §4.3b)."""

import sys

import pytest

from vouch.values import decode

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="needs sys.monitoring")

MODELS = '''
    import random


    def evaluate(dataset, model, seed=0, lr=1e-3):
        rng = random.Random(seed)
        return {"acc": {"resnet": 0.93, "vit": 0.91}[model] + rng.uniform(-0.01, 0.01)}


    def throughput(model, batch=64):
        return 1200.0


    class Trainer:
        def fit(self, epochs, **opts):
            return 0.5


    def batches(n):
        yield from range(n)
'''


def values(project, run_id="exp", timing=False):
    """The run's values; without ``timing``, only results (not the ``.time`` durations)."""
    got = project.record(run_id)["values"]
    return got if timing else {k: v for k, v in got.items() if not v.get("timing")}


def test_listed_functions_are_recorded_with_their_calls(project):
    project.write("vouch.toml", '''
        [[track]]
        function = "models.py::throughput"
        desc = "images per second"
        unit = "img/s"

        [[track]]
        function = "models.py::Trainer.fit"
        desc = "final loss"
    ''')
    project.write("models.py", MODELS)
    project.write("exp.py", '''
        import vouch
        from models import Trainer, throughput

        throughput("resnet")
        throughput("vit", batch=32)
        Trainer().fit(3, lr=0.1)
    ''')
    proc = project.run("exp.py", check=True)
    v = values(project)
    assert sorted(v) == ["Trainer.fit.epochs_3.lr_0_1", "throughput.resnet.batch_64",
                         "throughput.vit.batch_32"]
    t = v["throughput.resnet.batch_64"]
    assert t["value"] == 1200.0 and t["desc"] == "images per second" and t["unit"] == "img/s"
    assert t["site"] == "models.py:9"                                  # the definition
    assert len(t["call"].pop("seconds")) == 1
    assert t["call"] == {"function": "models.py::throughput",
                         "args": {"model": "resnet", "batch": 64}, "sites": ["exp.py:4"],
                         "via": "[[track]]"}
    assert "matched no function" not in proc.stderr


def test_over_and_key_template(project):
    project.write("vouch.toml", '''
        [[track]]
        function = "models.py::evaluate"
        over = "seed"
        key = "{dataset}.{model}"
        desc = "test accuracy"
        fmt = ".1pct"
    ''')
    project.write("models.py", MODELS)
    project.write("exp.py", '''
        import vouch
        from models import evaluate

        for model in ("resnet", "vit"):
            for seed in range(3):
                evaluate("cifar", model, seed=seed)
    ''')
    project.run("exp.py", check=True)
    v = values(project)
    assert sorted(v) == ["cifar.resnet.acc", "cifar.vit.acc"]
    s = decode(v["cifar.resnet.acc"]["type"], v["cifar.resnet.acc"]["value"])
    assert s.n == 3 and abs(s.mean - 0.93) < 0.01
    call = v["cifar.resnet.acc"]["call"]
    assert call["over"] == {"seed": [0, 1, 2]} and call["calls"] == 3
    assert call["args"] == {"dataset": "cifar", "model": "resnet", "lr": 0.001}
    assert call["sites"] == ["exp.py:6"]
    assert v["cifar.resnet.acc"]["fmt"] == ".1pct"


def test_returns_names_tuple_elements(project):
    project.write("vouch.toml", '''
        [[track]]
        function = "exp.py::boot"
        returns = ["mean", "std"]
        desc = "bootstrap accuracy ({-1})"
    ''')
    project.write("exp.py", '''
        import vouch

        def boot(model):
            return 0.9, 0.01

        boot("vit")
    ''')
    project.run("exp.py", check=True)
    v = values(project)
    assert v["boot.vit.mean"]["value"] == 0.9 and v["boot.vit.std"]["value"] == 0.01
    assert v["boot.vit.std"]["desc"] == "bootstrap accuracy (std)"
    assert v["boot.vit.std"]["call"]["via"] == "[[track]]"


def test_config_tracked_calls_are_timed(project):
    project.write("vouch.toml", '''
        [[track]]
        function = "exp.py::slow"
        time = true
        desc = "v"
    ''')
    project.write("exp.py", '''
        import time
        import vouch

        def slow(x):
            time.sleep(0.02)
            return float(x)

        slow(1)
    ''')
    project.run("exp.py", check=True)
    v = values(project, timing=True)
    assert 0.015 < v["slow.x_1"]["call"]["seconds"][0] < 5
    assert v["slow.x_1.time"]["value"] == v["slow.x_1"]["call"]["seconds"][0]


def test_a_decorated_function_is_recorded_once(project):
    project.write("vouch.toml", '[[track]]\nfunction = "exp.py::f"\n')
    project.write("exp.py", '''
        import vouch

        @vouch.track(desc="v")
        def f(x):
            return float(x)

        f(1)
    ''')
    project.run("exp.py", check=True)
    v = values(project)
    assert list(v) == ["f.x_1"] and v["f.x_1"]["call"]["sites"] == ["exp.py:7"]


def test_patterns_match_files_and_qualnames(project):
    project.write("vouch.toml", '''
        [[track]]
        function = ["throughput", "exp.py::local_*"]
        desc = "v"
    ''')
    project.write("models.py", MODELS)
    project.write("exp.py", '''
        import vouch
        from models import throughput

        def local_score(k):
            return k * 2.0

        throughput("resnet")
        local_score(2)
        [local_score(i) for i in (5,)]          # the comprehension itself is not a match
    ''')
    proc = project.run("exp.py", check=True)
    assert sorted(values(project)) == ["local_score.k_2", "local_score.k_5",
                                       "throughput.resnet.batch_64"]
    assert "vouch.toml" not in proc.stderr


def test_mistakes_are_reported_not_raised(project):
    project.write("vouch.toml", '''
        [[track]]
        function = "models.py::batches"

        [[track]]
        function = "models.py::throughput"
        over = "seed"

        [[track]]
        function = "models.py::evaluat"

        [[track]]
        function = "models.py::Trainer.fit"
        colour = "red"

        [[track]]
        function = "models.py::evaluate"
        returns = ["a", "a"]
    ''')
    project.write("models.py", MODELS)
    project.write("exp.py", '''
        import vouch
        from models import Trainer, batches, throughput

        list(batches(3))
        throughput("resnet")
        Trainer().fit(1)
        vouch.record("done", 1, desc="a value, so there is a run")
    ''')
    proc = project.run("exp.py", check=True)
    err = proc.stderr
    assert "batches is a generator" in err
    assert "over= names ['seed'], which throughput() doesn't take" in err
    assert "'models.py::evaluat' matched no function that ran" in err
    assert "unknown field(s) ['colour']" in err
    assert "returns= must be distinct names" in err
    assert "Trainer.fit.epochs_1" in values(project)       # a stray field doesn't stop it
    assert err.count("matched no function") == 1


def test_unmatched_rules_wait_for_the_end_of_the_script(project):
    project.write("vouch.toml", '[[track]]\nfunction = "models.py::throughput"\ndesc = "v"\n')
    project.write("models.py", MODELS)
    project.write("exp.py", '''
        import vouch
        from models import throughput

        with vouch.run("first"):
            vouch.record("a", 1, desc="a")
        with vouch.run("second"):
            throughput("resnet")
    ''')
    proc = project.run("exp.py", check=True)
    assert "matched no function" not in proc.stderr
    assert list(values(project, "second")) == ["throughput.resnet.batch_64"]


def test_without_tracking_it_says_so(project):
    project.write("vouch.toml", '[[track]]\nfunction = "models.py::throughput"\n')
    project.write("models.py", MODELS)
    project.write("exp.py", '''
        import vouch
        from models import throughput

        throughput("resnet")
        vouch.record("done", 1, desc="d")
    ''')
    proc = project.run("exp.py", env={"VOUCH_TRACE": "0"}, check=True)
    assert "[[track]] needs function tracking (tracking disabled (VOUCH_TRACE=0))" in proc.stderr
    assert list(values(project)) == ["done"]


def test_vouch_exec_runs_an_unmodified_script(project):
    project.write("vouch.toml", '''
        [[track]]
        function = "exp.py::evaluate"
        over = "seed"
        desc = "test accuracy"
    ''')
    project.write("exp.py", '''
        import sys


        def evaluate(dataset, seed=0):
            return 0.9 + seed / 100


        if __name__ == "__main__":
            for s in range(int(sys.argv[1])):
                evaluate("cifar", seed=s)
    ''')
    proc = project.run("-m", "vouch.exec", "exp.py", "4", check=True)
    rec = project.record("exp")
    assert rec["command"] == ["python", "-m", "vouch.exec", "exp.py", "4"]
    s = decode("stat", rec["values"]["evaluate.cifar"]["value"])
    assert s.n == 4
    # tracking started before the script's first line: it is tracked by function
    code = rec["code"]
    assert code.get("granularity", "function") == "function"
    assert "exp.py" not in (code.get("whole_files") or {})
    assert "recorded run exp" in proc.stderr


def test_vouch_exec_usage(project):
    proc = project.run("-m", "vouch.exec")
    assert proc.returncode == 2 and "usage: python -m vouch.exec" in proc.stderr
    proc = project.run("-m", "vouch.exec", "missing.py")
    assert proc.returncode == 2 and "no such script" in proc.stderr
