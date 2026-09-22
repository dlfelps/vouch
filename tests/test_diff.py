"""vouch diff: recorded values between git revisions, with size and direction of each move."""

import json
import shutil
import subprocess

import pytest
from conftest import edit, rerun

from vouch import cli
from vouch.config import Config
from vouch.index import Entry, Index
from vouch.values import Stat
from vouch.vdiff import compare

HAVE_GIT = shutil.which("git") is not None


def index(tmp_path, *entries):
    idx = Index(Config(tmp_path))
    for e in entries:
        idx.entries[e.key] = e
    return idx


def val(key, raw, **kw):
    return Entry(key, "value", raw, run=kw.pop("run", "r"), **kw)


def one(tmp_path, old, new, **kw):
    items, unchanged = compare(Config(tmp_path), index(tmp_path, *old), index(tmp_path, *new), **kw)
    return items, unchanged


# ---------------------------------------------------------------------------
# comparing two indexes
# ---------------------------------------------------------------------------

def test_added_removed_unchanged(tmp_path):
    items, unchanged = one(tmp_path, [val("a", 1.0), val("b", 2.0), val("c", 3.0)],
                           [val("a", 1.0 + 1e-15), val("c", 3.0), val("d", 4.0)])
    assert unchanged == 2                                     # float noise is not a change
    assert {(i.key, i.cls) for i in items} == {("b", "removed"), ("d", "added")}
    assert next(i for i in items if i.key == "d").new == "4"


def test_delta_direction_and_verdict(tmp_path):
    items, _ = one(tmp_path, [val("acc", 0.90, better="higher"), val("loss", 0.30, better="lower"),
                              val("epochs", 200)],
                   [val("acc", 0.72, better="higher"), val("loss", 0.29, better="lower"),
                    val("epochs", 300)])
    by = {i.key: i for i in items}
    acc = by["acc"]
    assert acc.direction == "down" and acc.verdict == "worse"
    assert acc.delta == pytest.approx(-0.18) and acc.rel == pytest.approx(-0.2)
    assert "large move" in " ".join(acc.reasons)
    assert by["loss"].direction == "down" and by["loss"].verdict == "better"
    assert by["epochs"].direction == "up" and by["epochs"].verdict is None
    assert by["epochs"].delta == 100
    # the worse one is read first
    assert [i.key for i in items][0] == "acc"


def test_percentages_move_in_points(tmp_path):
    items, _ = one(tmp_path, [val("acc", 0.932, fmt=".1pct")], [val("acc", 0.901, fmt=".1pct")])
    assert items[0].old == "93.2%" and items[0].new == "90.1%"
    assert items[0].delta_text() == "Δ -3.1 pts, -3.3% relative"


def test_stat_mean_and_sample_size(tmp_path):
    items, _ = one(tmp_path, [val("acc", Stat(0.8, 0.03, 5))], [val("acc", Stat(0.79, 0.03, 3))])
    assert items[0].delta == pytest.approx(-0.01)
    assert any("sample size changed (n=5 -> 3)" in r for r in items[0].reasons)


def test_zero_old_value_has_a_delta_but_no_relative(tmp_path):
    items, _ = one(tmp_path, [val("x", 0)], [val("x", 2)])
    assert items[0].delta == 2 and items[0].rel is None and items[0].delta_text() == "Δ +2"


def test_claims(tmp_path):
    def claim(holds, margin):
        return Entry("c", "claim", holds, run="r", extra={"margin": margin})
    items, _ = one(tmp_path, [claim(True, 0.2)], [claim(False, -0.1)])
    assert items[0].old == "HOLDS" and items[0].new == "FALSE" and items[0].verdict == "worse"
    assert items[0].reasons[0] == "NOW FALSE" and items[0].group == "claims"
    items, _ = one(tmp_path, [claim(True, 0.2)], [claim(True, 0.3)])
    assert items[0].verdict is None and items[0].reasons == ["margin 0.2 → 0.3"]


def test_cited_first_and_filters(tmp_path):
    old = [val("a.x", 1.0), val("b.y", 1.0)]
    new = [val("a.x", 2.0), val("b.y", 2.0)]
    items, _ = one(tmp_path, old, new, cited={"b.y": 2})
    assert [i.key for i in items] == ["b.y", "a.x"] and items[0].cited == 2
    items, unchanged = one(tmp_path, old, new, keep=lambda k: k.startswith("a."))
    assert [i.key for i in items] == ["a.x"]


def test_subfields_only_when_asked(tmp_path):
    old = [val("s", 1.0), Entry("s.mean", "stat-field", 1.0, run="r", parent="s")]
    new = [val("s", 2.0), Entry("s.mean", "stat-field", 2.0, run="r", parent="s")]
    assert [i.key for i in one(tmp_path, old, new)[0]] == ["s"]
    assert [i.key for i in one(tmp_path, old, new, subfields=True)[0]] == ["s", "s.mean"]


# ---------------------------------------------------------------------------
# against git
# ---------------------------------------------------------------------------

def git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(example):
    if not HAVE_GIT:
        pytest.skip("git not installed")
    assert cli.main(["build", "--root", str(example)]) == 0
    git(example, "init", "-q")
    git(example, "add", "-A")
    git(example, "commit", "-qm", "base")
    return example


def diff_json(root, capsys, *args):
    capsys.readouterr()
    assert cli.main(["diff", "--root", str(root), "--json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_diff_against_head(repo, capsys):
    data = diff_json(repo, capsys)
    assert data["command"] == "diff" and data["schema"] == "vouch/1"
    assert data["from"] == "HEAD" and data["to"] == "working tree" and data["items"] == []
    edit(repo / "train.py", "N_TEST = 200", "N_TEST = 50")
    rerun(repo)
    data = diff_json(repo, capsys)
    by = {i["key"]: i for i in data["items"]}
    n = by["toy.centroid.n_test"]
    assert (n["old"], n["new"], n["delta"], n["direction"]) == ("200", "50", -150, "down")
    assert n["cited"] and n["class"] == "changed" and n["group"] == "run train"
    assert "toy.majority.n_test" in by and not by["toy.majority.n_test"]["cited"]
    assert data["counts"]["changed"] == len(data["items"])
    # only cited keys
    assert all(i["cited"] for i in diff_json(repo, capsys, "--cited")["items"])
    # only some keys
    assert [i["key"] for i in diff_json(repo, capsys, "--key", "*.majority.n_test")["items"]] == \
        ["toy.majority.n_test"]


def test_diff_two_revisions_and_text(repo, capsys):
    edit(repo / "train.py", "N_TEST = 200", "N_TEST = 50")
    rerun(repo)
    git(repo, "commit", "-qam", "fewer test points")
    assert diff_json(repo, capsys, "HEAD")["items"] == []
    data = diff_json(repo, capsys, "HEAD~1", "HEAD")
    assert data["from"] == "HEAD~1" and data["to"] == "HEAD" and data["counts"]["changed"] >= 2
    capsys.readouterr()
    assert cli.main(["diff", "--root", str(repo), "HEAD~1"]) == 0
    out = capsys.readouterr().out
    assert "vouch diff: HEAD~1" in out and "toy.centroid.n_test" in out and "200" in out
    md = repo / "diff.md"
    assert cli.main(["diff", "--root", str(repo), "HEAD~1", "--md", str(md)]) == 0
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# Recorded values: `HEAD~1`") and "**`toy.centroid.n_test`**" in text


def test_diff_new_run_and_removed_run(repo, capsys):
    (repo / ".vouch" / "runs" / "train.json").rename(repo / "train.json.bak")
    data = diff_json(repo, capsys)
    assert data["items"] and all(i["class"] == "removed" for i in data["items"])


def test_diff_errors(repo, capsys, tmp_path):
    assert cli.main(["diff", "--root", str(repo), "no-such-rev"]) == 2
    assert "unknown revision" in capsys.readouterr().err
    assert cli.main(["diff", "--root", str(repo), "a", "b", "c"]) == 2
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "vouch.toml").write_text("", encoding="utf-8")
    assert cli.main(["diff", "--root", str(plain)]) == 2
    assert "git" in capsys.readouterr().err
