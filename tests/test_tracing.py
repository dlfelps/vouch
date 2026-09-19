"""Function-level tracking (SPEC §8.2), on real runs in throwaway projects."""

import sys
import textwrap

import pytest
from conftest import edit

from vouch.config import Config
from vouch.freshness import assess
from vouch.store import load_runs
from vouch.units import MODULE, unit_of_qualname

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="needs sys.monitoring")

LIB = '''
SCALE = 2


def used(x):
    return x * SCALE


def unused(x):
    return -x


class Model:
    depth = 3
    key = staticmethod(lambda x: x)

    def __init__(self, k):
        self.k = k

    def forward(self, x):
        def inner(y):
            return y * self.k
        return inner(x) * self.depth

    def other(self):
        return 0
'''

EXP = '''
import vouch
from lib import Model, used


def helper(x):
    return used(x) + 1


def never_called():
    return 0


with vouch.run("exp") as run:
    run.record("v", helper(1) + Model(2).forward(3), desc="v")
'''


def code(project, run="exp"):
    return project.record(run)["code"]


def state(project, run="exp"):
    cfg = Config.load(project.root)
    return assess(cfg, load_runs(cfg.store), check_env=False)[run]


def test_only_executed_functions_are_recorded(project):
    project.write("lib.py", LIB)
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    c = code(project)
    assert c["granularity"] == "function" and "whole_files" not in c and "why" not in c
    assert set(c["units"]) == {
        "exp.py::<module>", "exp.py::helper",
        "lib.py::<module>", "lib.py::Model", "lib.py::Model.__init__",
        "lib.py::Model.forward", "lib.py::used"}


def test_editing_code_the_run_never_executed_stays_fresh(project):
    project.write("lib.py", LIB)
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    edit(project.root / "lib.py", "return -x", "return -2 * x")                  # unused
    edit(project.root / "lib.py", "def other(self):\n        return 0",
         "def other(self):\n        return 1")                                     # Model.other
    edit(project.root / "exp.py", "def never_called():\n    return 0",
         "def never_called():\n    return 1")
    st = state(project)          # files changed, but nothing this run executed: passes
    assert st.state == "cosmetic" and not st.is_error

    edit(project.root / "lib.py", "return y * self.k", "return y * self.k + 1")   # nested in forward
    st = state(project)
    assert st.state == "stale" and [r.subject for r in st.reasons] == ["lib.py::Model.forward"]


def test_class_attributes_and_module_constants_count(project):
    project.write("lib.py", LIB)
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    edit(project.root / "lib.py", "depth = 3", "depth = 4")
    assert [r.subject for r in state(project).reasons] == ["lib.py::Model"]
    edit(project.root / "lib.py", "depth = 4", "depth = 3")
    edit(project.root / "lib.py", "SCALE = 2", "SCALE = 3")
    assert [r.subject for r in state(project).reasons] == ["lib.py::<module>"]


def test_module_imported_before_vouch_is_tracked_whole(project):
    project.write("lib.py", LIB)
    project.write("exp.py", "from lib import used\n" + EXP)
    project.run("exp.py", check=True)
    c = code(project)
    assert c["whole_files"] == {"lib.py": "imported before vouch started tracking"}
    assert "lib.py::unused" in c["units"] and "lib.py::Model.other" in c["units"]
    assert "exp.py::never_called" not in c["units"]           # the script itself is still precise


def test_code_before_import_vouch_makes_the_script_whole(project):
    project.write("lib.py", LIB)
    project.write("exp.py", "def warmup():\n    return 1\n\n\nW = warmup()\n" + EXP)
    project.run("exp.py", check=True)
    c = code(project)
    assert c["whole_files"] == {"exp.py": "code ran before `import vouch`"}
    assert "exp.py::never_called" in c["units"]


def test_harmless_code_before_import_vouch_keeps_the_script_precise(project):
    """Definitions, stdlib and third-party calls before `import vouch` can't run our code."""
    project.write("lib.py", LIB)
    project.write("exp.py", textwrap.dedent('''
        import json
        import sys
        from pathlib import Path

        import pandas as pd

        ROOT = Path(__file__).parent
        sys.path.insert(0, str(ROOT))
        FRAME = pd.DataFrame({"a": [1]})
        CONFIG = json.loads('{"k": 1}')


        def spare(x=len("abc")):
            return x


        class Box:
            size = max(1, 2)
    ''') + EXP)
    project.run("exp.py", check=True)
    c = code(project)
    assert "whole_files" not in c, c.get("whole_files")
    assert "exp.py::spare" not in c["units"] and "exp.py::Box" in c["units"]


def test_first_party_decorator_before_import_vouch(project):
    project.write("lib.py", LIB + "\n\ndef register(f):\n    return f\n")
    project.write("exp.py", "from lib import register\n\n\n@register\ndef task():\n    return 1\n"
                  + EXP)
    project.run("exp.py", check=True)
    assert code(project)["whole_files"]["exp.py"] == "code ran before `import vouch`"


def test_import_inside_a_function(project):
    project.write("lib.py", LIB)
    project.write("exp.py", '''
        def main():
            import vouch
            from lib import used
            with vouch.run("exp") as run:
                run.record("v", used(1), desc="v")


        def spare():
            return 0


        main()
    ''')
    project.run("exp.py", check=True)
    c = code(project)
    assert c["whole_files"]["exp.py"] == "vouch was imported from inside a function"
    assert "lib.py::unused" not in c["units"]


def test_child_processes_fall_back_to_module_granularity(project):
    project.write("lib.py", LIB)
    project.write("exp.py", '''
        import multiprocessing as mp
        import vouch
        from lib import used


        def work(x):
            return used(x)


        if __name__ == "__main__":
            with vouch.run("exp") as run:
                p = mp.Process(target=work, args=(1,))
                p.start()
                p.join()
                run.record("v", used(1), desc="v")
    ''')
    project.run("exp.py", check=True)
    c = code(project)
    assert c["granularity"] == "module"
    assert c["why"] == "the run started child processes, which are not tracked"
    assert "lib.py::unused" in c["units"]


def test_python_subprocesses_count_as_children_but_other_programs_do_not(project):
    project.write("lib.py", LIB)
    project.write("exp.py", '''
        import subprocess, sys
        import vouch
        from lib import used

        with vouch.run("exp") as run:
            if sys.argv[1] == "python":
                subprocess.run([sys.executable, "-c", "print(1)"], check=True)
            else:
                subprocess.run(["git", "--version"], capture_output=True)
            run.record("v", used(1), desc="v")
    ''')
    project.run("exp.py", "other", check=True)
    assert code(project)["granularity"] == "function"
    project.run("exp.py", "python", check=True)
    assert code(project)["granularity"] == "module"


def test_tracking_disabled_or_configured_off(project):
    project.write("lib.py", LIB)
    project.write("exp.py", EXP)
    project.run("exp.py", env={"VOUCH_TRACE": "0"}, check=True)
    c = code(project)
    assert c["granularity"] == "module" and c["why"] == "tracking disabled (VOUCH_TRACE=0)"

    project.write("vouch.toml", '[freshness]\ngranularity = "module"\n')
    project.run("exp.py", check=True)
    c = code(project)
    assert c["granularity"] == "module" and "why" not in c


def test_no_free_monitoring_tool(project):
    project.write("lib.py", LIB)
    project.write("exp.py", "import sys\nfor t in (2, 3, 4, 5):\n    sys.monitoring.use_tool_id(t, 'other')\n" + EXP)
    project.run("exp.py", check=True)
    c = code(project)
    assert c["granularity"] == "module" and c["why"] == "no free sys.monitoring tool id"


def test_file_edited_while_the_run_is_in_progress(project):
    """The record holds the code that ran, not what is on disk when the run ends."""
    project.write("lib.py", LIB)
    project.write("exp.py", '''
        import pathlib
        import vouch
        from lib import used

        with vouch.run("exp") as run:
            v = used(1)
            p = pathlib.Path("lib.py")                  # someone edits the model mid-run
            p.write_text(p.read_text().replace("return x * SCALE", "return x * SCALE * 10"))
            run.record("v", v, desc="v")
    ''')
    proc = project.run("exp.py", check=True)
    assert "lib.py changed while the run was in progress" in proc.stderr
    st = state(project)
    assert st.state == "stale" and [r.subject for r in st.reasons] == ["lib.py::used"]
    edit(project.root / "lib.py", "return x * SCALE * 10", "return x * SCALE")
    assert state(project).state == "fresh"


def test_functions_run_in_threads_are_recorded(project):
    project.write("lib.py", LIB)
    project.write("exp.py", '''
        import threading
        import vouch
        from lib import used, unused

        with vouch.run("exp") as run:
            out = []
            t = threading.Thread(target=lambda: out.append(unused(2)))
            t.start()
            t.join()
            run.record("v", out[0], desc="v")
    ''')
    project.run("exp.py", check=True)
    assert "lib.py::unused" in code(project)["units"]


def test_unit_of_qualname_edge_cases():
    assert unit_of_qualname("Model.<lambda>") == "Model"
    assert unit_of_qualname("<generic parameters of f>") == MODULE
    assert unit_of_qualname("Model.fit.<locals>.<listcomp>") == "Model.fit"
    assert unit_of_qualname("A.B.m") == "A.B.m"
