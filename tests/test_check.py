"""vouch check / status / trace / ls / accept / changes / ack / hook -- end to end."""

import csv
import io
import json
import os
import subprocess
import sys

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


def with_provenance_csv(root):
    edit(root / "vouch.toml", 'main = "paper/main.tex"\n',
         'main = "paper/main.tex"\nprovenance_csv = "paper/vouch-provenance.csv"\n')


def test_prose_edit_that_moves_citations_says_so(example, capsys):
    """A paper edit that only shifts citations to new lines leaves the provenance CSV's
    `cited_at` column stale. check used to say the CSV "does not match the recorded values",
    which sends the reader looking for a moved value when none moved."""
    with_provenance_csv(example)
    built(example)
    edit(example / "paper/main.tex", "\\maketitle\n", "\\maketitle\n\nAn added sentence.\n")
    code, rep = check_json(example, capsys)
    issue = next(i for i in rep["issues"] if i["check"] == "out-of-sync")
    assert "recorded values" not in issue["message"]
    assert "citations moved" in issue["message"] and "no value changed" in issue["message"]
    assert issue["fix"] == {"kind": "build", "value": "vouch build"}
    # Only the line numbers are stale, and every value is right: a warning, which the next
    # build clears. As an error it failed every check, and every commit, after a prose edit.
    assert issue["severity"] == "warning" and code == 0


def moved(example):
    """The example built, then its centroid classifier changed and re-run: its values have
    moved, and no build has seen them yet."""
    built(example)
    edit(example / "models.py", CENTROID, MANHATTAN)
    rerun(example)


def test_build_reports_a_change_in_full_once_then_as_a_count(example, capsys):
    """Changes awaiting acknowledgment were printed in full, with their sentences, on every
    build: one paper had 46 of them, hundreds of lines on each build, burying anything new."""
    moved(example)
    capsys.readouterr()
    assert cli.main(["build", "--root", str(example)]) == 0
    first = capsys.readouterr().out
    assert "CHANGED VALUES" in first and "toy.centroid.acc" in first
    assert cli.main(["build", "--root", str(example)]) == 0
    again = capsys.readouterr().out
    assert "CHANGED VALUES" not in again and "Nearest-centroid reaches" not in again
    assert "still unacknowledged" in again and "vouch changes" in again


def test_check_collapses_changes_a_build_already_reported(example, capsys):
    moved(example)
    assert cli.main(["build", "--root", str(example)]) == 0       # reports them
    capsys.readouterr()
    code, out = check(example, capsys=capsys)
    assert code == 0
    assert "toy.centroid.acc:" not in out                        # no line per value ...
    assert "already reported" in out and "vouch changes" in out   # ... one for them all
    code, rep = check_json(example, capsys)                       # JSON keeps every one
    assert any(i["check"] in ("changed", "suspicious") and i["subject"] == "toy.centroid.acc"
               for i in rep["issues"])


def test_check_prints_changes_no_build_has_reported(example, capsys):
    moved(example)
    assert cli.main(["build", "--root", str(example), "--no-notify"]) == 0
    capsys.readouterr()
    code, out = check(example, capsys=capsys)
    assert "toy.centroid.acc:" in out and "already reported" not in out


def test_a_failed_check_names_its_errors_last(example, capsys):
    """Errors print first, so a long list of warnings scrolled them away, and the last line
    said only "FAILED: 1 error(s)"."""
    code, out = check(example, capsys=capsys)                     # never built
    assert code == 1
    last = out.strip().splitlines()[-1]
    assert last.startswith("FAILED") and "out-of-sync" in last


def test_hand_edited_csv_value_still_reads_as_a_value_mismatch(example, capsys):
    with_provenance_csv(example)
    built(example)
    csv_path = example / "paper/vouch-provenance.csv"
    text = csv_path.read_text(encoding="utf-8")
    assert "80.2" in text
    csv_path.write_text(text.replace("80.2", "85.0", 1), encoding="utf-8", newline="\n")
    code, rep = check_json(example, capsys)
    issue = next(i for i in rep["issues"] if i["check"] == "out-of-sync")
    assert "does not match the recorded values" in issue["message"]


RECORD_EXTRA = 'vouch.record("toy.extra", 1.0, desc="an extra value")\n'


def cite(example, key):
    edit(example / "paper/main.tex", "\\maketitle\n", f"\\maketitle\nExtra: \\vouch{{{key}}}.\n")


def about(rep, key):
    return [i for i in rep["issues"] if i.get("subject") == key]


def test_a_key_a_script_records_before_it_has_run_is_pending(example, capsys):
    """A key cited while the run that records it had yet to happen (a 20-minute run in
    flight) was "not recorded by any run": an error, and a blocking one in the edit hook,
    which pushed toward deleting the citation or inventing a vouch.expect(). The script
    names the key, so it is pending, and the producer is the run's re-run command."""
    built(example)
    edit(example / "train.py", "vouch.params(", RECORD_EXTRA + "vouch.params(")
    cite(example, "toy.extra")
    code, rep = check_json(example, capsys)
    issues = about(rep, "toy.extra")
    assert [i["check"] for i in issues] == ["pending"] and issues[0]["severity"] == "warning"
    assert "train.py:" in issues[0]["message"]
    assert issues[0]["fix"] == {"kind": "command", "value": "python train.py"}
    assert cli.main(["build", "--root", str(example), "--no-notify"]) == 0
    values = (example / "paper/vouch-values.tex").read_text(encoding="utf-8")
    assert r"\vouch@pending{toy.extra}{python train.py}" in values
    capsys.readouterr()
    code, rep = check_json(example, capsys, "--strict")
    assert code == 1 and about(rep, "toy.extra")[0]["severity"] == "error"


def test_a_key_a_new_script_records_is_pending_on_that_script(example, capsys):
    built(example)
    (example / "extra.py").write_text("import vouch\nvouch.record('side.value', 2.0, desc='d')\n",
                                      encoding="utf-8")
    cite(example, "side.value")
    code, rep = check_json(example, capsys)
    issue = about(rep, "side.value")[0]
    assert issue["check"] == "pending" and "extra.py:2" in issue["message"]
    assert issue["fix"]["value"] == "python extra.py"


def test_a_key_its_current_run_did_not_record_stays_unknown(example, capsys):
    """If the script's run is current and still did not record the key, re-running will not
    either (a branch it never takes): the key is unknown, and the message says where the
    script names it."""
    edit(example / "train.py", "vouch.params(", "if False:\n    " + RECORD_EXTRA + "vouch.params(")
    rerun(example)
    built(example)
    cite(example, "toy.extra")
    code, rep = check_json(example, capsys)
    issue = about(rep, "toy.extra")[0]
    assert issue["check"] == "unknown-key" and "train.py:" in issue["message"]


def marker(example, run, pid, entry="train.py"):
    d = example / ".vouch/cache/running"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run}.json").write_text(json.dumps(
        {"run": run, "pid": pid, "started": "2026-10-21T16:48:00Z", "entry": entry,
         "command": f"python {entry}"}), encoding="utf-8")


def ended_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_status_shows_a_run_in_progress_and_one_that_never_finished(example, capsys):
    built(example)
    marker(example, "train", os.getpid())
    marker(example, "sweep", ended_pid(), entry="sweep.py")
    capsys.readouterr()
    assert cli.main(["status", "--root", str(example), "--no-env"]) == 0
    out = capsys.readouterr().out
    assert "train" in out and "running since 2026-10-21 16:48" in out
    assert "sweep" in out and "interrupted" in out
    assert cli.main(["status", "--root", str(example), "--no-env", "--json"]) == 0
    running = json.loads(capsys.readouterr().out)["running"]
    assert {(r["run"], r["alive"]) for r in running} == {("train", True), ("sweep", False)}


def test_a_pending_key_says_its_run_is_in_progress(example, capsys):
    built(example)
    edit(example / "train.py", "vouch.params(", RECORD_EXTRA + "vouch.params(")
    cite(example, "toy.extra")
    marker(example, "train", os.getpid())
    code, rep = check_json(example, capsys)
    assert "running since 2026-10-21 16:48" in about(rep, "toy.extra")[0]["message"]


def test_the_edit_hook_does_not_block_on_a_key_a_script_records(example):
    from vouch.edithook import run_edit_hook
    built(example)
    edit(example / "train.py", "vouch.params(", RECORD_EXTRA + "vouch.params(")
    cite(example, "toy.extra")
    payload = json.dumps({"tool_name": "Edit",
                          "tool_input": {"file_path": str(example / "paper/main.tex")}})
    code, text = run_edit_hook(payload)
    assert "toy.extra" not in text and code == 0, text


def test_the_edit_hook_tells_the_agent_of_a_pending_key_without_blocking(example, capsys,
                                                                        monkeypatch):
    """A pending key is not a problem, so the hook must not block on it; but the agent should
    hear that the number is owed, to tell the user. Exit 2 blocks; stdout JSON with
    hookSpecificOutput.additionalContext reaches the agent without blocking."""
    import io
    from vouch.edithook import edit_hook_result
    built(example)
    edit(example / "train.py", "vouch.params(", RECORD_EXTRA + "vouch.params(")
    cite(example, "toy.extra")
    payload = json.dumps({"tool_name": "Edit",
                          "tool_input": {"file_path": str(example / "paper/main.tex")}})
    code, err, out = edit_hook_result(payload)
    assert code == 0 and err == ""
    hso = json.loads(out)["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "toy.extra" in hso["additionalContext"] and "train.py" in hso["additionalContext"]
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))          # the command, end to end
    capsys.readouterr()
    assert cli.main(["hook", "claude"]) == 0
    printed = capsys.readouterr()
    assert json.loads(printed.out)["hookSpecificOutput"]["additionalContext"] and not printed.err



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


def test_check_one_paper_of_several(project, capsys):
    # SPEC §12 and docs/cli/check.md list `vouch check --paper FILE`; the parser had no
    # such option, so a project with several papers could not gate one of them.
    project.write("vouch.toml", '[[paper]]\nmain = "paper/a.tex"\n\n'
                  '[[paper]]\nmain = "paper/b.tex"\n')
    project.write("exp.py", 'import vouch\nvouch.record("m.acc", 0.9, desc="accuracy")\n')
    project.run("exp.py", check=True)
    for name, key in (("a", "m.acc"), ("b", "nope.x")):
        project.write(f"paper/{name}.tex", "\\documentclass{article}\n\\usepackage{vouch}\n"
                      "\\begin{document}\n\\vouch{" + key + "}\n\\end{document}\n")
    build(Config.load(project.root), notify=False)
    code, rep = check_json(project.root, capsys)
    assert code == 1 and rep["summary"]["papers"] == ["paper/a.tex", "paper/b.tex"]
    code, rep = check_json(project.root, capsys, "--paper", "paper/a.tex")
    assert code == 0, rep["issues"]
    assert rep["summary"]["papers"] == ["paper/a.tex"]
    code, rep = check_json(project.root, capsys, "--paper", "paper/b.tex")
    assert code == 1 and rep["summary"]["papers"] == ["paper/b.tex"]
    capsys.readouterr()
    assert cli.main(["check", "--root", str(project.root), "--no-env",
                     "--paper", "paper/c.tex"]) == 2              # not a paper of this project
    assert "paper/c.tex" in capsys.readouterr().err


def test_an_outdated_vouch_sty_is_reported(example, capsys):
    # vouch init copies vouch.sty beside the paper. When vouch is upgraded the copy
    # stays behind, silently: a paper built against an old sty could lack a macro the
    # new docs describe (\vouchpending), and check said nothing.
    built(example)
    sty = example / "paper" / "vouch.sty"
    current = sty.read_text(encoding="utf-8")
    sty.write_bytes(current.replace("\n", "\r\n").encode("utf-8"))      # a CRLF checkout
    code, rep = check_json(example, capsys)
    assert code == 0 and not [i for i in rep["issues"] if i["check"] == "sty-outdated"]
    sty.write_text(current.replace(r"\vouchpending{\detokenize{#1}}",
                                   r"\fbox{\scriptsize pending: \detokenize{#1}}"),
                   encoding="utf-8", newline="\n")
    code, rep = check_json(example, capsys)
    outdated = [i for i in rep["issues"] if i["check"] == "sty-outdated"]
    assert len(outdated) == 1 and outdated[0]["severity"] == "warning"
    assert "paper/vouch.sty" in outdated[0]["message"]
    assert outdated[0]["fix"]["value"] == "vouch init"
    assert check(example, "--strict", capsys=capsys)[0] == 1
    assert cli.main(["init", "--root", str(example)]) == 0
    capsys.readouterr()
    code, rep = check_json(example, capsys)
    assert not [i for i in rep["issues"] if i["check"] == "sty-outdated"]
