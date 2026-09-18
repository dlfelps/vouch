"""vouch check / status / trace / ls / accept / changes / ack / hook -- end to end."""

import csv
import io
import json
import subprocess

import pytest
from conftest import edit, rerun

from vouch import cli
from vouch.build import build
from vouch.config import Config
from vouch.store import write_record

CENTROID = "        return min(cent, key=lambda y: (x[0] - cent[y][0]) ** 2 + (x[1] - cent[y][1]) ** 2)"
MANHATTAN = "        return min(cent, key=lambda y: abs(x[0] - cent[y][0]) + abs(x[1] - cent[y][1]))"


def check(root, *extra, capsys=None):
    code = cli.main(["check", "--root", str(root), "--no-env", *extra])
    out = capsys.readouterr().out if capsys else ""
    return code, out


def check_json(root, capsys, *extra):
    code = cli.main(["check", "--root", str(root), "--no-env", "--json", *extra])
    return code, json.loads(capsys.readouterr().out)


def built(example):
    build(Config.load(example), notify=False)
    return example


def test_fresh_project_passes_strict(example, capsys):
    built(example)
    code, out = check(example, "--strict", capsys=capsys)
    assert code == 0, out
    assert "1/1 cited runs fresh" in out and "OK" in out


def test_not_built_is_out_of_sync(example, capsys):
    code, rep = check_json(example, capsys)
    assert code == 1
    assert {i["check"] for i in rep["issues"] if i["severity"] == "error"} == {"out-of-sync"}
    assert rep["issues"][0]["fix"] == {"kind": "build", "value": "vouch build"}


def test_code_edit_makes_the_cited_run_stale_but_not_out_of_sync(example, capsys):
    built(example)
    edit(example / "models.py", CENTROID, MANHATTAN)
    code, rep = check_json(example, capsys)
    assert code == 1 and not rep["ok"]
    errors = [i for i in rep["issues"] if i["severity"] == "error"]
    assert [i["check"] for i in errors] == ["stale"]         # freshness never reads as out-of-sync
    e = errors[0]
    assert "models.py::nearest_centroid changed since the run" in e["message"]
    assert e["fix"]["value"].startswith("python train.py")
    assert {"file": "paper/main.tex", "line": 15} in e["where"]
    assert e["detail"]["reasons"][0]["subject"] == "models.py::nearest_centroid"


def test_rerun_moves_values_then_ack(example, capsys):
    built(example)
    edit(example / "models.py", CENTROID, MANHATTAN)
    rerun(example)

    code, rep = check_json(example, capsys)          # re-run but not rebuilt
    assert "out-of-sync" in {i["check"] for i in rep["issues"] if i["severity"] == "error"}

    assert cli.main(["build", "--root", str(example), "--no-notify"]) == 0
    out = capsys.readouterr().out
    assert "CHANGED VALUES" in out and "Δ -3.1 pts" in out
    assert 'paper/main.tex:15  "Nearest-centroid reaches \\vouch{toy.centroid.acc} accuracy' in out

    code, out = check(example, capsys=capsys)
    assert code == 0 and "! changed" in out            # a warning ...
    code, out = check(example, "--strict", capsys=capsys)
    assert code == 1 and "✗ changed" in out.replace("x changed", "✗ changed")   # ... an error under --strict

    values = (example / "paper/vouch-values.tex").read_text(encoding="utf-8")
    line = next(ln for ln in values.splitlines() if ln.startswith(r"\vouch@set{toy.centroid.acc}{}"))
    assert line.endswith("}{1}") and "CHANGED: was 80.2 +/- 3.5\\% (acked" in line
    capsys.readouterr()
    assert cli.main(["export", "--root", str(example)]) == 0
    rows = {r["key"]: r for r in csv.DictReader(io.StringIO(capsys.readouterr().out))}
    assert rows["toy.centroid.acc"]["change_status"] == "changed"
    assert rows["toy.centroid.acc"]["previous_value"] == r"80.2 +/- 3.5\%"

    assert cli.main(["ack", "--root", str(example), "--all", "--why", "prose still true"]) == 0
    capsys.readouterr()
    code, out = check(example, "--strict", capsys=capsys)
    assert code == 0, out
    values = (example / "paper/vouch-values.tex").read_text(encoding="utf-8")
    line = next(ln for ln in values.splitlines() if ln.startswith(r"\vouch@set{toy.centroid.acc}{}"))
    assert line.endswith("}{0}")


def test_hand_edited_values_file_is_out_of_sync(example, capsys):
    built(example)
    edit(example / "paper/vouch-values.tex", r"{80.2 \pm 3.5}", r"{85.0 \pm 3.5}")
    code, rep = check_json(example, capsys)
    assert code == 1 and rep["issues"][0]["check"] == "out-of-sync"


def test_false_claim(example, capsys):
    rec_path = example / ".vouch/runs/train.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    rec["claims"]["toy.centroid_beats_majority"]["holds"] = False
    write_record(example / ".vouch", rec)
    built(example)
    code, rep = check_json(example, capsys)
    assert code == 1
    fc = [i for i in rep["issues"] if i["check"] == "false-claim"]
    assert fc and fc[0]["where"] == [{"file": "paper/main.tex", "line": 18}]


def test_uncited_stale_run_is_only_a_note(example, capsys):
    (example / "other.py").write_text(
        "import vouch\nwith vouch.run('other') as r:\n    r.record('o.v', 1, desc='o')\n",
        encoding="utf-8")
    rerun(example, "other.py")
    built(example)
    edit(example / "other.py", "r.record('o.v', 1", "r.record('o.v', 2")
    code, rep = check_json(example, capsys)
    assert code == 0
    stale = [i for i in rep["issues"] if i["check"] == "stale"]
    assert stale and stale[0]["severity"] == "info"


def test_accept_via_cli(example, capsys):
    built(example)
    edit(example / "models.py", "    ones = sum(y for _, y in train)\n    label = int(ones * 2",
         "    positives = sum(y for _, y in train)\n    label = int(positives * 2")
    assert check(example)[0] == 1
    assert cli.main(["accept", "train", "--root", str(example), "--why", "renamed a local"]) == 0
    assert "renamed a local" in (example / ".vouch/accepted.toml").read_text(encoding="utf-8")
    capsys.readouterr()
    code, out = check(example, "--strict", capsys=capsys)
    assert code == 0, out


def test_status_ls_trace(example, capsys):
    built(example)
    assert cli.main(["status", "--root", str(example), "--json", "--no-env"]) == 0
    runs = json.loads(capsys.readouterr().out)["runs"]
    assert runs[0]["run"] == "train" and runs[0]["state"] == "fresh" and runs[0]["cited"] > 0

    assert cli.main(["ls", "--root", str(example), "--json", "*.acc"]) == 0
    keys = {k["key"]: k for k in json.loads(capsys.readouterr().out)["keys"]}
    assert keys["toy.centroid.acc"]["cited"] == 1 and keys["main.centroid.acc"]["cited"] == 1
    assert keys["toy.centroid.acc"]["value"] == "80.2 +/- 3.5%"

    assert cli.main(["trace", "--root", str(example), "toy.centroid.acc"]) == 0
    out = capsys.readouterr().out
    assert "recorded  train.py:25   in run train" in out and "cited     paper/main.tex:15" in out
    assert cli.main(["trace", "--root", str(example), "paper/main.tex:18"]) == 0
    assert "\\vouchclaim{toy.centroid_beats_majority}" in capsys.readouterr().out
    assert cli.main(["trace", "--root", str(example), "models.py"]) == 0
    assert "run train (fresh) uses models.py" in capsys.readouterr().out


def test_changes_markdown_report(example, capsys, tmp_path):
    built(example)
    edit(example / "models.py", CENTROID, MANHATTAN)
    rerun(example)
    build(Config.load(example), notify=False)
    md = tmp_path / "review.md"
    assert cli.main(["changes", "--root", str(example), "--md", str(md)]) == 0
    text = md.read_text(encoding="utf-8")
    assert "## `toy.centroid.acc` — changed" in text and "was **80.2 +/- 3.5%**" in text


def test_figures_untracked_and_tampered(example, capsys):
    (example / "fig.py").write_text(
        "import vouch\nwith vouch.run('figs') as r:\n"
        "    open('paper/curve.pdf', 'wb').write(b'%PDF-1.4 fake')\n"
        "    r.artifact('paper/curve.pdf')\n", encoding="utf-8")
    rerun(example, "fig.py")
    (example / "paper/untracked.pdf").write_bytes(b"%PDF-1.4 other")
    edit(example / "paper/main.tex", r"\end{document}",
         "\\includegraphics{curve}\\includegraphics{untracked}\n\\end{document}")
    built(example)
    code, rep = check_json(example, capsys)
    assert code == 0
    assert [i["check"] for i in rep["issues"] if i["severity"] == "warning"] == ["figure-untracked"]
    (example / "paper/curve.pdf").write_bytes(b"%PDF-1.4 edited by hand")
    code, rep = check_json(example, capsys)
    assert code == 1
    assert "figure-tampered" in {i["check"] for i in rep["issues"] if i["severity"] == "error"}


def test_severity_overrides(example, capsys):
    built(example)
    (example / "vouch.toml").write_text((example / "vouch.toml").read_text(encoding="utf-8")
                                        + '\n[check]\nseverity = { stale = "warning" }\n',
                                        encoding="utf-8")
    edit(example / "models.py", CENTROID, MANHATTAN)
    assert check(example)[0] == 0
    assert check(example, "--strict")[0] == 1


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def test_hook_install(example, capsys):
    if git(example, "init", "-q").returncode != 0:
        pytest.skip("git not available")
    assert cli.main(["hook", "install", "--root", str(example)]) == 0
    hook = example / ".git/hooks/pre-commit"
    text = hook.read_text(encoding="utf-8")
    assert "# vouch pre-commit hook" in text and "check --quiet" in text
    assert cli.main(["hook", "install", "--root", str(example), "--strict"]) == 0   # ours: replaced
    assert "check --quiet --strict" in hook.read_text(encoding="utf-8")
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["hook", "install", "--root", str(example)]) == 2
    assert "already exists and is not vouch's" in capsys.readouterr().err
    assert cli.main(["hook", "install", "--root", str(example), "--force"]) == 0
