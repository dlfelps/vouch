"""Source annotations: managed ``% vouch: key=value`` comments (SPEC §7.8)."""

from vouch import cli
from vouch.build import build
from vouch.check import run_check
from vouch.config import Config
from vouch.tex.annotate import rewrite


def test_rewrite_owns_only_its_suffix():
    text = "a \\vouch{k} b\nplain % a comment\nkeep % vouch: ignore\nold  % vouch: k=1\n"
    new = rewrite(text, {1: "k=2", 3: "k=3"})
    assert new.split("\n") == ["a \\vouch{k} b  % vouch: k=2", "plain % a comment",
                               "keep % vouch: ignore  % vouch: k=3", "old", ""]
    assert rewrite(new, {1: "k=2", 3: "k=3"}) == new                       # idempotent
    assert rewrite(new, {}, strip=True) == \
        "a \\vouch{k} b\nplain % a comment\nkeep % vouch: ignore\nold\n"
    assert rewrite("x\r\ny\r\n", {1: "k=1"}) == "x  % vouch: k=1\r\ny\r\n"   # CRLF kept


def test_build_keeps_annotations_current(example, capsys):
    toml = example / "vouch.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[latex]\nannotate = true\n",
                    encoding="utf-8")
    tex = example / "paper" / "main.tex"
    res = build(Config.load(example))
    assert tex in res.written
    lines = tex.read_text(encoding="utf-8").split("\n")
    line15 = next(ln for ln in lines if ln.startswith("Nearest-centroid reaches"))
    assert line15.endswith("  % vouch: toy.centroid.acc=80.2 ± 3.5%")
    claim = next(ln for ln in lines if ln.startswith("\\vouchclaim{toy.centroid_beats_majority}"))
    assert claim.endswith("% vouch: toy.centroid_beats_majority=HOLDS")
    fmt = next(ln for ln in lines if "as a fraction" in ln)
    assert "toy.centroid.acc.mean=0.802" in fmt
    assert tex not in build(Config.load(example)).written                    # nothing moved
    assert run_check(Config.load(example), check_env=False).ok              # comments only
    capsys.readouterr()
    assert cli.main(["sync", "--root", str(example), "--strip"]) == 0
    assert "stripped annotations from paper/main.tex" in capsys.readouterr().out
    assert "% vouch:" not in tex.read_text(encoding="utf-8")
