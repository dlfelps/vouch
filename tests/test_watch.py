"""vouch watch: rebuild when values, values modules or the paper change; never on its own output."""

import os
import sys
import time

from conftest import edit, rerun

from vouch import cli
from vouch.watch import Watcher


def bump(path):
    """Make a write visible even on filesystems with coarse timestamps."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_new_values_trigger_a_rebuild(example):
    w = Watcher(example)
    first = w.rebuild()
    assert first.error is None and first.moved == [] and first.result.written
    assert w.scan() == []                               # the build's own writes are not changes
    edit(example / "train.py", "N_TEST = 200", "N_TEST = 50")
    rerun(example)
    bump(example / ".vouch" / "runs" / "train.json")
    assert [p.name for p in w.scan()] == ["train.json"]
    rb = w.rebuild()
    moved = {d.key: d for d in rb.moved}
    assert moved["toy.centroid.n_test"].old == "200" and moved["toy.centroid.n_test"].new == "50"
    assert moved["toy.centroid.n_test"].cited
    assert "50" in (example / "paper" / "vouch-values.tex").read_text(encoding="utf-8")
    assert w.scan() == []


def test_paper_and_values_module_are_watched_outputs_are_not(example):
    w = Watcher(example)
    w.rebuild()
    bump(example / "paper" / "vouch-values.tex")             # generated: ignored
    assert w.scan() == []
    bump(example / "paper" / "main.tex")
    assert [p.name for p in w.scan()] == ["main.tex"]
    bump(example / "vouch_values.py")
    assert [p.name for p in w.scan()] == ["vouch_values.py"]


def test_a_broken_edit_does_not_stop_it(example):
    w = Watcher(example)
    w.rebuild()
    (example / "vouch.toml").write_text("[[paper]\n", encoding="utf-8")
    rb = w.rebuild()
    assert rb.error and rb.result is None
    edit(example / "vouch.toml", "[[paper]\n", '[[paper]]\nmain = "paper/main.tex"\n')
    assert w.rebuild().error is None


def test_then_runs_only_after_a_build_that_wrote_files(example):
    marker = example / "then.txt"
    cmd = f'"{sys.executable}" -c "open(\'then.txt\', \'a\').write(\'x\')"'
    w = Watcher(example, then=cmd)
    assert w.rebuild().then_rc == 0 and marker.read_text() == "x"
    assert w.rebuild().then_rc is None and marker.read_text() == "x"      # nothing written


def test_the_loop(example, capsys, monkeypatch):
    w = Watcher(example, notify=False)
    reports = []
    edits = iter([lambda: (edit(example / "train.py", "N_TEST = 200", "N_TEST = 50"),
                           rerun(example), bump(example / ".vouch" / "runs" / "train.json"))])

    def report(changed, rb):
        reports.append((changed, rb))
        for e in edits:                 # one edit after the first build
            e()
            break
    w.run(report, interval=0.01, cycles=3)
    assert len(reports) == 2 and reports[0][0] == []
    assert [p.name for p in reports[1][0]] == ["train.json"] and reports[1][1].moved


def test_cli_prints_and_stops_on_ctrl_c(example, capsys, monkeypatch):
    calls = []

    def sleep(_):
        calls.append(1)
        raise KeyboardInterrupt
    monkeypatch.setattr(time, "sleep", sleep)
    assert cli.main(["watch", "--root", str(example), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "vouch watch: building" in out and "wrote" in out and "stopped" in out
