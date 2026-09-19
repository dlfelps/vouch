"""examples/tutorial works exactly as the tutorial tells it (the basis of the docs).

The reader copies ``start/`` (a plain experiment and a paper draft), makes the
edits the tutorial shows, and ends with what is in ``finished/``. These tests take
the same steps in a temporary copy, so the tutorial can't drift from the code.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vouch import cli

pytest.importorskip("numpy")
pytest.importorskip("matplotlib")

TUTORIAL = Path(__file__).resolve().parents[1] / "examples" / "tutorial"
START, FINISHED = TUTORIAL / "start", TUTORIAL / "finished"
IGNORE = shutil.ignore_patterns(".vouch", "figures", "*.pdf", "*.aux", "*.log", "*.out", "*.fls",
                                "*.fdb_latexmk", "*.up?", "vouch-*", "vouch.sty", ".gitattributes",
                                "__pycache__")


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VOUCH_")}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, *args], cwd=root, env=env, capture_output=True,
                          text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def vouch(root: Path, *args: str, capsys) -> tuple[int, str]:
    code = cli.main([*args, "--root", str(root)])
    return code, capsys.readouterr().out


def take(name: str, root: Path) -> None:
    """One tutorial step: the file as it is in finished/."""
    shutil.copyfile(FINISHED / name, root / name)


def test_the_experiment_gains_only_vouch_lines():
    """Step 2 of the tutorial: three additions, nothing else in the experiment changes."""
    before = (START / "experiment.py").read_text(encoding="utf-8").splitlines()
    after = (FINISHED / "experiment.py").read_text(encoding="utf-8").splitlines()
    added = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        assert op in ("equal", "insert"), f"{op}: {before[i1:i2]} -> {after[j1:j2]}"
        if op == "insert":
            added += [ln.strip() for ln in after[j1:j2] if ln.strip()]
    assert added == [
        "import vouch",
        '@vouch.track(over="seed", returns=("acc", "train_acc"))',
        'vouch.params({"sizes": SIZES, "seeds": len(SEEDS), "n_test": N_TEST,',
        '"noise": NOISE, "k": K})',
    ]


def test_the_paper_and_the_draft_share_their_frame():
    """The finished paper is the draft with results written in, plus \\usepackage{vouch}."""
    draft = (START / "paper" / "main.tex").read_text(encoding="utf-8")
    paper = (FINISHED / "paper" / "main.tex").read_text(encoding="utf-8")
    title = re.search(r"\\title\{.*\}", draft).group(0)
    assert title in paper and "\\usepackage{vouch}" in paper
    assert "\\vouch" not in draft and "{vouch}" not in draft          # no vouch yet


def readme_matches(root: Path, values: str, capsys) -> None:
    """Every key the tutorial text cites exists, and its "You write | The PDF shows"
    table shows what vouch renders."""
    import json
    readme = (TUTORIAL / "README.md").read_text(encoding="utf-8")
    assert cli.main(["ls", "--json", "--root", str(root)]) == 0
    known = {k["key"] for k in json.loads(capsys.readouterr().out)["keys"]}
    cited = set(re.findall(r"\\vouch(?:claim|table|raw)?(?:\[[^\]]*\])?\{([^}]+)\}", readme))
    assert cited - {"key"} <= known, sorted(cited - known)
    rows = re.findall(r"^\| `\\vouch(?:\[([^\]]*)\])?\{([^}]+)\}` \| ([^|(]+?)\s*(?:\(.*\)\s*)?\|$",
                      readme, re.M)
    assert len(rows) == 6
    for fmt, key, shown in rows:
        m = re.search(r"\\vouch@set\{" + re.escape(key) + r"\}\{" + re.escape(fmt) +
                      r"\}\{[^\n]*?\}\{([^{}]*)\}", values)
        assert m, (key, fmt)
        assert m.group(1).replace("+/-", "±").replace("\\%", "%") == shown, key


@pytest.fixture
def tutorial(tmp_path) -> Path:
    root = tmp_path / "my-paper"
    shutil.copytree(START, root, ignore=IGNORE)
    return root


def test_the_tutorial_end_to_end(tutorial, capsys):
    root = tutorial
    # 1. the plain experiment runs, and knows nothing of vouch
    out = run(root, "experiment.py").stdout
    assert "knn  n=640  test accuracy 0.873 +/- 0.014" in out
    assert (root / "paper" / "figures" / "learning_curve.pdf").is_file()
    assert not (root / ".vouch").exists()

    # 2. vouch init, then the three added lines
    assert vouch(root, "init", capsys=capsys)[0] == 0
    assert (root / "vouch.toml").is_file() and (root / "paper" / "vouch.sty").is_file()
    take("experiment.py", root)
    proc = run(root, "experiment.py")
    assert "recorded run experiment: 36 value(s), 1 artifact(s)" in proc.stderr
    nodesc = [ln for ln in proc.stderr.splitlines() if "without desc" in ln]
    assert len(nodesc) == 1 and "*.acc (12), *.train_acc (12)" in nodesc[0]
    code, out = vouch(root, "ls", capsys=capsys)
    assert re.search(r"evaluate\.knn\.n_train_640\.acc\s+0\.873 \+/- 0\.0141", out)
    assert out.index("n_train_20.acc") < out.index("n_train_160.acc")      # numbers in order
    assert "subfields of mean ± std values not shown" in out.replace("+/-", "±")
    assert re.search(r"experiment\.param\.sizes\s+20, 40, 80, 160, 320, 640", out)

    # 3. describe the metrics once, in vouch.toml: no re-run needed
    take("vouch.toml", root)
    code, out = vouch(root, "cite", "evaluate.knn.n_train_640.acc", capsys=capsys)
    assert code == 0 and "\\vouch{evaluate.knn.n_train_640.acc}" in out
    assert "87.3 ± 1.4%" in out.replace("+/-", "±")
    assert "test accuracy of knn (n_train_640)" in out

    # 4-6. the paper, and the numbers computed from numbers
    take("vouch_values.py", root)
    take("paper/main.tex", root)
    assert vouch(root, "build", capsys=capsys)[0] == 0
    code, out = vouch(root, "check", "--strict", capsys=capsys)
    assert code == 0, out
    values = (root / "paper" / "vouch-values.tex").read_text(encoding="utf-8")
    for key, shown in [("crossover", "80"), ("gap", "5.8"), ("best_possible", "90\\%"),
                       ("learning_curve.640.gap", "\\ensuremath{+5.8}"),
                       ("evaluate.knn.n_train_20.acc", "\\ensuremath{59.0 \\pm 10.0}\\%")]:
        assert f"\\vouch@set{{{key}}}{{}}{{{shown}}}" in values, key
    assert "\\vouch@set{experiment.param.noise}{.0pct}{10\\%}" in values
    code, out = vouch(root, "trace", "paper/figures/learning_curve.pdf", capsys=capsys)
    assert code == 0 and re.search(r"saved\s+experiment\.py:\d+\s+in run experiment \(fresh\)", out)
    assert "as the run saved it" in out and re.search(r"cited\s+paper/main\.tex:\d+", out)
    readme_matches(root, values, capsys)

    # 7. the code changes: K = 1 memorises the flipped labels
    exp = root / "experiment.py"
    exp.write_text(exp.read_text(encoding="utf-8").replace("K = 15 ", "K = 1  "), encoding="utf-8")
    code, out = vouch(root, "check", capsys=capsys)
    assert code == 1 and "stale" in out and "figure-stale" in out
    run(root, "experiment.py")
    code, out = vouch(root, "build", capsys=capsys)
    assert "NOW FALSE   knn_wins_large" in out and "NOW FALSE   linear_wins_small" in out
    assert "table learning_curve   12 cells changed" in out
    assert "crossover is cited here, but its definition failed" in out
    code, out = vouch(root, "check", capsys=capsys)
    assert code == 1
    assert out.count("false-claim") == 2 and "derive-error" in out and "unknown-key" not in out

    # ... and back: the same numbers as before, nothing left to acknowledge
    exp.write_text(exp.read_text(encoding="utf-8").replace("K = 1  ", "K = 15 "), encoding="utf-8")
    run(root, "experiment.py")
    assert vouch(root, "build", capsys=capsys)[0] == 0
    code, out = vouch(root, "check", "--strict", capsys=capsys)
    assert code == 0, out
    assert "no pending changes" in vouch(root, "changes", capsys=capsys)[1]

    # 8. the PDF
    if shutil.which("latexmk") is None or shutil.which("pdftotext") is None:
        return
    paper = root / "paper"
    subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                   cwd=paper, capture_output=True, check=True)
    log = (paper / "main.log").read_text(encoding="utf-8", errors="replace")
    assert "Package vouch Warning" not in log and "undefined" not in log.lower()
    text = subprocess.run(["pdftotext", "main.pdf", "-"], cwd=paper, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout
    assert "from 80 training points on and finishes 5.8 points ahead" in " ".join(text.split())
    assert "Value provenance" in text
