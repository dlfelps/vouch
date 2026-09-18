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
