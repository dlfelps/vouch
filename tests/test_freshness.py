"""Freshness states, on real runs in throwaway projects (SPEC §8)."""

import json

from conftest import edit

from vouch.config import Config
from vouch.freshness import acceptable_hashes, assess, load_ledger, write_ledger
from vouch.store import load_runs, write_record


def states(project):
    cfg = Config.load(project.root)
    return assess(cfg, load_runs(cfg.store), check_env=False)


def make(project):
    project.write("lib.py", '''
        """Helpers."""
        SCALE = 2.0

        def score(x):
            return SCALE * x

        def unused():
            return 0
    ''')
    project.write("data/in.csv", "a\n1\n")
    project.write("exp.py", '''
        import vouch
        from lib import score
        with vouch.run("exp") as run:
            run.input("data/in.csv")
            run.record("k", score(1.0), desc="k")
    ''')
    project.run("exp.py", check=True)


def test_fresh_then_cosmetic_then_stale(project):
    make(project)
    assert states(project)["exp"].state == "fresh"
    edit(project.root / "lib.py", '"""Helpers."""', '"""Helpers, reworded."""')
    edit(project.root / "lib.py", "def score(x):", "def score(x):  # doubled")
    assert states(project)["exp"].state == "cosmetic"
    edit(project.root / "lib.py", "return SCALE * x", "return SCALE * x + 1")
    st = states(project)["exp"]
    assert st.state == "stale"
    assert [r.subject for r in st.of("unit-changed")] == ["lib.py::score"]
    assert st.summary() == "lib.py::score changed since the run"


def test_input_changes_and_absence(project):
    make(project)
    (project.root / "data/in.csv").write_text("a\n2\n", encoding="utf-8")
    st = states(project)["exp"]
    assert st.state == "stale" and st.of("input-changed")[0].subject == "data/in.csv"
    (project.root / "data/in.csv").unlink()
    st = states(project)["exp"]
    assert st.state == "fresh" and st.of("absent-input")      # absent is a warning, not stale


def test_deleted_file_and_renamed_function(project):
    make(project)
    edit(project.root / "lib.py", "def score(x):\n    return SCALE * x",
         "def score2(x):\n    return SCALE * x")
    st = states(project)["exp"]
    assert st.state == "stale" and st.of("unit-missing")[0].subject == "lib.py::score"
    (project.root / "lib.py").unlink()
    assert states(project)["exp"].of("file-missing")


def test_upstream_and_regenerated_inputs(project):
    project.write("prep.py", '''
        import sys, vouch
        with vouch.run("prep") as run:
            open("mid.csv", "w").write("x\\n" + sys.argv[1] + "\\n")
            run.artifact("mid.csv")
    ''')
    project.write("fit.py", '''
        import vouch
        with vouch.run("fit") as run:
            run.input("mid.csv")
            run.record("fit.v", float(open("mid.csv").read().split()[1]), desc="v")
    ''')
    project.run("prep.py", "1", check=True)
    project.run("fit.py", check=True)
    s = states(project)
    assert s["prep"].state == "fresh" and s["fit"].state == "fresh"

    # the producer re-ran with a different output: the consumer's input was regenerated
    project.run("prep.py", "2", check=True)
    s = states(project)
    assert s["prep"].state == "fresh"
    assert s["fit"].state == "stale" and s["fit"].of("input-regenerated")[0].detail == "prep"

    # the producer's code changes: the consumer is upstream-stale
    project.run("fit.py", check=True)
    edit(project.root / "prep.py", '"x\\n"', '"y\\n"')
    s = states(project)
    assert s["prep"].state == "stale" and s["fit"].state == "upstream-stale"


def test_a_run_that_read_a_value_is_stale_once_the_value_moves(project):
    # A figure drawn from a measurement's numbers, in a run of its own, read them through a
    # side file. Read with vouch.value, the dependency is the value: a re-run that moves
    # another value leaves the figure fresh, and one that moves a value it drew does not.
    project.write("measure.py", '''
        import sys, vouch
        with vouch.run("measure"):
            vouch.record("m.count", int(sys.argv[1]), desc="pairs")
            vouch.record("m.seconds", float(sys.argv[2]), desc="wall time")
    ''')
    project.write("figure.py", '''
        import vouch
        with vouch.run("figure"):
            vouch.value("m.count")
    ''')
    project.run("measure.py", "7", "1.0", check=True)
    project.run("figure.py", check=True)
    s = states(project)
    assert s["measure"].state == "fresh" and s["figure"].state == "fresh"

    project.run("measure.py", "7", "2.0", check=True)          # only the time moved
    assert states(project)["figure"].state == "fresh"

    project.run("measure.py", "8", "2.0", check=True)          # the count it drew moved
    s = states(project)
    assert s["figure"].state == "stale"
    r = s["figure"].of("value-changed")[0]
    assert (r.subject, r.detail) == ("m.count", "measure")
    assert s["figure"].summary() == "value m.count (run measure) changed since the run"

    project.run("figure.py", check=True)
    assert states(project)["figure"].state == "fresh"
    edit(project.root / "measure.py", "int(sys.argv[1])", "int(sys.argv[1]) + 0")
    s = states(project)                                         # the producer's code changed
    assert s["measure"].state == "stale" and s["figure"].state == "upstream-stale"

    edit(project.root / "measure.py", 'vouch.record("m.count"', 'vouch.record("m.pairs"')
    project.run("measure.py", "8", "2.0", check=True)          # the key it read is gone
    s = states(project)
    assert s["figure"].state == "stale" and s["figure"].of("value-missing")


def test_tampered_artifact(project):
    project.write("exp.py", '''
        import vouch
        with vouch.run("exp") as run:
            open("out.csv", "w").write("1\\n")
            run.artifact("out.csv")
            run.record("v", 1, desc="v")
    ''')
    project.run("exp.py", check=True)
    (project.root / "out.csv").write_text("2\n", encoding="utf-8")
    st = states(project)["exp"]
    assert st.state == "tampered" and st.of("tampered")[0].subject == "out.csv"


def test_accept_covers_exact_hashes_and_reopens(project):
    make(project)
    edit(project.root / "lib.py", "return SCALE * x", "return x * SCALE")
    cfg = Config.load(project.root)
    st = states(project)["exp"]
    assert st.state == "stale"
    write_ledger(cfg, {"exp": {"run": "exp", "why": "commutative", "by": "t", "date": "d",
                               "hashes": acceptable_hashes(st)}})
    assert load_ledger(cfg)["exp"]["why"] == "commutative"
    assert states(project)["exp"].state == "accepted"
    edit(project.root / "lib.py", "return x * SCALE", "return x * SCALE * 1.0")
    assert states(project)["exp"].state == "stale"


def test_env_drift_and_incomplete(project):
    make(project)
    cfg = Config.load(project.root)
    path = project.root / ".vouch/runs/exp.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["env"]["packages"] = {"definitely-not-installed-pkg": "1.0"}
    write_record(cfg.store, rec)
    st = assess(cfg, load_runs(cfg.store))["exp"]
    assert st.state == "fresh" and "not installed now" in st.of("env-drift")[0].detail
    rec["status"] = "partial"
    write_record(cfg.store, rec)
    assert states(project)["exp"].state == "incomplete"


def test_an_unknown_interpreter_is_not_drift(project):
    """`vouch run` of a non-Python command and `vouch import` record python as None.

    The interpreter is unknown, not different, and such a run must not be reported as
    drifted against whatever Python runs the check (it was, as "recorded with Python
    None, now 3.13.5", on every run of that kind)."""
    make(project)
    cfg = Config.load(project.root)
    path = project.root / ".vouch/runs/exp.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["env"] = {"python": None, "platform": rec["env"].get("platform"), "packages": {}}
    write_record(cfg.store, rec)
    st = assess(cfg, load_runs(cfg.store))["exp"]
    assert st.state == "fresh" and not st.of("env-drift")
    rec["env"]["python"] = "2.7.18"                  # a known, different interpreter still is
    write_record(cfg.store, rec)
    assert assess(cfg, load_runs(cfg.store))["exp"].of("env-drift")
