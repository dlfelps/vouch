"""The LLM layer (SPEC §13): expect/todo, catalog, search, cite, compare, suggest, hooks, agents."""

import json
import math
import sys
import types

import pytest

from vouch import cli
from vouch.build import build, plan
from vouch.check import run_check
from vouch.config import Config

EXP = '''
    import vouch

    @vouch.track(over="seed", desc="top-1 test accuracy")
    def evaluate(dataset, model, seed=0):
        base = {"resnet": 0.93, "vit": 0.91, "mlp": 0.85}[model]
        return {"acc": base + seed / 1000, "loss": 1 - base}

    for m in ("resnet", "vit", "mlp"):
        for s in range(5):
            evaluate("cifar", m, seed=s)
    vouch.params({"epochs": 90, "lr": 0.001})
    vouch.record("cifar.n_test", 10000, desc="test images")
'''


@pytest.fixture
def proj(project, monkeypatch):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n'
                                '[metrics]\n"*.acc" = { fmt = ".1pct", better = "higher" }\n'
                                '"*.loss" = { better = "lower", desc = "test loss" }\n')
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    monkeypatch.chdir(project.root)
    return project


def paper(project, body: str) -> None:
    project.write("paper/main.tex", "\\documentclass{article}\n\\usepackage{vouch}\n"
                  "\\begin{document}\n" + body + "\n\\end{document}\n")


def issues(project, strict=False):
    rep = run_check(Config.load(project.root), strict=strict, check_env=False)
    out = {}
    for i in rep.issues:
        out.setdefault(i.check, []).append(i)
    return out, rep


# ---------------------------------------------------------------------------
# placeholders
# ---------------------------------------------------------------------------

def test_expect_makes_a_number_owed_not_invented(proj, capsys):
    proj.write("vouch_values.py", '''
        import vouch

        vouch.expect("imagenet.vit.acc", desc="ViT top-1 on ImageNet",
                     producer="python exp_imagenet.py")

        @vouch.derive("imagenet.gap", fmt=".1f", desc="gap")
        def gap(v):
            return 100 * (v["evaluate.cifar.resnet.acc.mean"] - v["imagenet.vit.acc"])
    ''')
    paper(proj, "ViT gets \\vouch{imagenet.vit.acc}, a gap of \\vouch{imagenet.gap}.")
    build(Config.load(proj.root))
    got, rep = issues(proj)
    msgs = {i.subject: i for i in got["pending"]}
    assert set(msgs) == {"imagenet.vit.acc", "imagenet.gap"}
    assert msgs["imagenet.vit.acc"].fix == "python exp_imagenet.py"
    assert "computed from imagenet.vit.acc" in msgs["imagenet.gap"].message
    assert "unknown-key" not in got and rep.ok                     # a warning in a draft
    assert not issues(proj, strict=True)[1].ok                     # an error under --strict
    values = (proj.root / "paper/vouch-values.tex").read_text(encoding="utf-8")
    assert "\\vouch@pending{imagenet.vit.acc}{python exp\\_imagenet.py}" in values
    assert "\\vouch@pending{imagenet.gap}" in values
    capsys.readouterr()
    assert cli.main(["todo"]) == 0
    out = capsys.readouterr().out
    assert "1 value(s) the paper is still owed:" in out and "run: python exp_imagenet.py" in out
    assert "also blocks imagenet.gap" in out and "cited at paper/main.tex:4" in out
    catalog = (proj.root / ".vouch/CATALOG.md").read_text(encoding="utf-8")
    assert "## pending (vouch todo)" in catalog and "imagenet.vit.acc · ViT top-1 on ImageNet" in catalog
    # the experiment runs: everything resolves at the next build
    proj.write("exp_imagenet.py", "import vouch\nvouch.record('imagenet.vit.acc', 0.8, desc='vit')\n")
    proj.run("exp_imagenet.py", check=True)
    assert "imagenet.vit.acc changed (read by imagenet.gap)" in \
        issues(proj)[0]["out-of-sync"][0].message
    build(Config.load(proj.root))
    got, rep = issues(proj, strict=True)
    assert rep.ok and "pending" not in got
    assert [i.subject for i in got["expectation-met"]] == ["imagenet.vit.acc"]


# ---------------------------------------------------------------------------
# the catalog
# ---------------------------------------------------------------------------

def test_catalog_lists_everything_citable(example):
    build(Config.load(example))
    text = (example / ".vouch/CATALOG.md").read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[0] == "# vouch catalog — 7 values · 2 claims · 2 tables"
    assert "Never type a number. Never compute with numbers in prose." in text
    assert ("toy.centroid.acc · 80.2 ± 3.5% · test accuracy of centroid, mean and std over seeds · "
            "higher↑ · train · fresh · cited 1×") in lines
    assert any(ln.startswith("toy.gain · 30.3 · ") and " · derived · " in ln for ln in lines)
    assert any(ln.startswith("toy.gain_over_10 · HOLDS (30.3 > 10, margin 203.0%)") for ln in lines)
    assert "centroid.* → toy.centroid.*" in lines
    assert not any(ln.startswith("toy.centroid.acc.mean") for ln in lines)   # subfields: header


def test_big_catalogs_split_by_prefix(proj):
    rows = ", ".join(f'"k{i}": {i}' for i in range(310))
    proj.write("many.py", "import vouch\nvouch.record_all({" + rows + "}, prefix='big', desc='d')\n")
    proj.run("many.py", check=True)
    paper(proj, "x")
    build(Config.load(proj.root))
    text = (proj.root / ".vouch/CATALOG.md").read_text(encoding="utf-8")
    assert "## index (one file per prefix under .vouch/catalog/)" in text
    assert "big · 310 keys · .vouch/catalog/big.md" in text
    assert (proj.root / ".vouch/catalog/big.md").read_text(encoding="utf-8").count("\nbig.k") == 310


# ---------------------------------------------------------------------------
# search, cite, compare
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("vit accuracy cifar", "evaluate.cifar.vit.acc"),
    ("resnet top-1", "evaluate.cifar.resnet.acc"),
    ("mlp test loss", "evaluate.cifar.mlp.loss"),
    ("number of test images", "cifar.n_test"),
    ("learning rate", "exp.param.lr"),
    ("how long does vit take", "evaluate.cifar.vit.time"),
    ("vit accuracy standard deviation", "evaluate.cifar.vit.acc.std"),
])
def test_search_ranks_the_intended_key_first(proj, query, expected):
    from vouch.assist import search
    paper(proj, "x")
    ctx = plan(Config.load(proj.root), check_env=False)
    hits = search(ctx, query)
    assert hits and hits[0]["key"] == expected, [h["key"] for h in hits[:3]]


def test_cite_gives_the_exact_snippet(proj, capsys):
    paper(proj, "x")
    build(Config.load(proj.root))
    capsys.readouterr()
    assert cli.main(["cite", "evaluate.cifar.resnet.acc"]) == 0
    out = capsys.readouterr().out.split("\n")
    assert out[0].startswith("\\vouch{evaluate.cifar.resnet.acc}") and "93.2 ± 0.2%" in out[0]
    assert out[1].startswith("\\vouch[.2pct]{evaluate.cifar.resnet.acc}")
    assert "top-1 test accuracy · higher is better · fresh · run exp" in out[2]
    assert out[3].startswith("subfields: .mean 93.2% · .std 0.2% · .n 5")
    assert cli.main(["cite", "evaluate.cifar.resnet.acc", "--fmt", ".3f"]) == 0
    assert "\\vouch[.3f]{evaluate.cifar.resnet.acc}  →  0.932 ± 0.002" in capsys.readouterr().out
    assert cli.main(["cite", "evaluate.cifar.resnt.acc"]) == 1
    assert "did you mean evaluate.cifar.resnet.acc" in capsys.readouterr().err
    assert cli.main(["cite", "cifar.n_test", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["snippets"][0] == {"latex": "\\vouch{cifar.n_test}", "renders": "10000",
                                  "fmt": "(default)"}


def test_student_t_and_welch():
    from vouch.assist import betainc, welch
    from vouch.values import Stat
    assert betainc(1, 1, 0.3) == pytest.approx(0.3)
    assert betainc(2, 3, 0.4) == pytest.approx(0.5248, abs=1e-4)
    # two-sided p of t = 2.0 with 10 dof is 0.0734
    assert betainc(5, 0.5, 10 / (10 + 4)) == pytest.approx(0.07339, abs=1e-4)
    t, dof, p = welch(Stat(1.0, 0.5, 10), Stat(0.5, 0.5, 10))
    assert t == pytest.approx(2.2361, abs=1e-3) and dof == pytest.approx(18)
    assert p == pytest.approx(0.0382, abs=1e-3)
    assert welch(Stat(1, 0, 1), Stat(1, 0, 1)) is None


def test_compare_writes_what_makes_it_citable(proj, capsys):
    paper(proj, "x")
    assert cli.main(["compare", "evaluate.cifar.resnet.acc", "evaluate.cifar.vit.acc"]) == 0
    out = capsys.readouterr().out
    assert "(higher is better → evaluate.cifar.resnet.acc is better)" in out
    assert "difference +0.02 (2 points)" in out and "Welch t-test p" in out
    assert '@vouch.derive("evaluate.cifar.resnet_vs_vit.pts", fmt=".1f", unit="points"' in out
    assert 'return vouch.gt(v["evaluate.cifar.resnet.acc.mean"], v["evaluate.cifar.vit.acc.mean"])' in out
    assert cli.main(["compare", "evaluate.cifar.resnet.acc", "evaluate.cifar.vit.acc", "--write"]) == 0
    assert "wrote evaluate.cifar.resnet_vs_vit.pts and evaluate.cifar.resnet_beats_vit to " \
           "vouch_values.py" in capsys.readouterr().out
    paper(proj, "\\vouchclaim{evaluate.cifar.resnet_beats_vit}{ResNet wins} by "
                "\\vouch{evaluate.cifar.resnet_vs_vit.pts} points.")
    build(Config.load(proj.root))
    values = (proj.root / "paper/vouch-values.tex").read_text(encoding="utf-8")
    assert "\\vouch@set{evaluate.cifar.resnet_vs_vit.pts}{}{2.0}" in values
    assert issues(proj, strict=True)[1].ok
    assert cli.main(["compare", "evaluate.cifar.resnet.acc", "evaluate.cifar.vit.acc", "--write"]) == 0
    assert "already defined" in capsys.readouterr().out
    # lower is better: the winner is the smaller loss
    assert cli.main(["compare", "evaluate.cifar.mlp.loss", "evaluate.cifar.resnet.loss",
                     "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["winner"] == "evaluate.cifar.resnet.loss"
    assert got["claim_key"] == "evaluate.cifar.resnet_beats_mlp" and "vouch.lt(" in got["code"]


# ---------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------

def test_suggest_classifies_and_applies(proj, capsys):
    proj.write("dup.py", "import vouch\nvouch.record('a.x', 0.77, desc='a')\n"
                         "vouch.record('b.x', 0.77, desc='b')\n")
    proj.run("dup.py", check=True)
    body = ("ResNet reaches 93.2\\% on 10{,}000 images ($0.852$ for the MLP).\n"
            "Two things are 0.77 and one is 0.123.\n")
    proj.write("paper/main.tex", "\\documentclass{article}\n\\usepackage{vouch}\n\\begin{document}\n"
               + body + "\\end{document}\n")
    (proj.root / "paper/main.tex").write_bytes(
        (proj.root / "paper/main.tex").read_bytes().replace(b"\n", b"\r\n"))
    build(Config.load(proj.root))
    capsys.readouterr()
    assert cli.main(["suggest"]) == 0
    out = capsys.readouterr().out
    assert "93.2\\%       → \\vouch[.1pct]{evaluate.cifar.resnet.acc.mean}   exact" in out
    assert "→ ambiguous: a.x, b.x -- choose by hand" in out
    assert "0.123        → NO SOURCE" in out
    assert "5 literal(s): 3 replaceable (--apply), 1 no-source, 1 ambiguous" in out
    assert cli.main(["suggest", "--apply"]) == 0
    raw = (proj.root / "paper/main.tex").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")      # CRLF kept
    text = raw.decode()
    assert ("ResNet reaches \\vouch[.1pct]{evaluate.cifar.resnet.acc.mean} on "
            "\\vouch[d]{cifar.n_test} images ($\\vouch[.3f]{evaluate.cifar.mlp.acc.mean}$") in text
    assert "0.77 and one is 0.123" in text                                # never touched
    build(Config.load(proj.root))
    got, _ = issues(proj)
    assert {i.subject for i in got.get("bare-number", [])} == {"0.77"}
    assert {i.subject for i in got.get("no-source", [])} == {"0.123"}


# ---------------------------------------------------------------------------
# Claude Code hooks and init --agents
# ---------------------------------------------------------------------------

def _payload(path) -> str:
    return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}})


def test_edit_hook_catches_slips_in_the_same_turn(proj):
    from vouch.edithook import run_edit_hook
    paper(proj, "x")
    build(Config.load(proj.root))
    proj.write("paper/results.tex", "Accuracy is 93.2\\% and \\vouch{evaluate.cifar.resnt.acc}.\n"
                                    "Also \\vouchclaim{k}. And 44.4\\% from nowhere.\n")
    code, text = run_edit_hook(_payload(proj.root / "paper/results.tex"))
    assert code == 2
    assert "paper/results.tex has 5 problem(s)" in text and "unknown key k:" in text
    assert "line 1: 93.2% is typed by hand; it is evaluate.cifar.resnet.acc.mean: replace it " \
           "with \\vouch[.1pct]{evaluate.cifar.resnet.acc.mean}" in text
    assert "unknown key evaluate.cifar.resnt.acc" in text and "did you mean" in text
    assert "\\vouchclaim takes two arguments" in text
    assert "44.4% matches no recorded value" in text
    proj.write("paper/results.tex", "Accuracy is \\vouch{evaluate.cifar.resnet.acc}.\n")
    assert run_edit_hook(_payload(proj.root / "paper/results.tex")) == (0, "")
    assert run_edit_hook(_payload(proj.root / "exp.py")) == (0, "")               # not tex
    assert run_edit_hook("not json") == (0, "")


def test_edit_hook_ignores_what_is_not_a_vouch_paper(tmp_path):
    from vouch.edithook import run_edit_hook
    (tmp_path / "x.tex").write_text("93.2\\%", encoding="utf-8")
    assert run_edit_hook(_payload(tmp_path / "x.tex")) == (0, "")


def test_entry_point_skips_everything_for_non_tex_edits(monkeypatch, capsys):
    import io

    from vouch import _entry
    monkeypatch.setattr(sys, "argv", ["vouch", "hook", "claude"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"file_path": "a.py"}})))
    assert _entry.main() == 0


def test_stop_hook_blocks_until_check_passes(proj):
    from vouch.edithook import run_stop_hook
    paper(proj, "Typed 0.123 here.")
    build(Config.load(proj.root))
    code, text = run_stop_hook("{}", proj.root)
    assert code == 2 and "no-source" in text
    assert run_stop_hook(json.dumps({"stop_hook_active": True}), proj.root) == (0, "")
    paper(proj, "\\vouch{cifar.n_test} images.")
    build(Config.load(proj.root))
    assert run_stop_hook("{}", proj.root) == (0, "")


def test_init_agents_shows_then_writes(proj, capsys, monkeypatch):
    monkeypatch.setattr("vouch.agents.shutil.which", lambda name: "vouch")
    proj.write("CLAUDE.md", "# Project\n\nOur own notes.\n")
    proj.write(".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
    paper(proj, "x")
    assert cli.main(["init", "--agents"]) == 0                    # not a tty, no --yes: a diff
    out = capsys.readouterr().out
    assert "+name: vouch" in out and "nothing written; re-run with --yes" in out
    assert not (proj.root / ".claude/skills/vouch/SKILL.md").exists()
    assert cli.main(["init", "--agents", "--yes", "--stop-gate"]) == 0
    skill = (proj.root / ".claude/skills/vouch/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: vouch\ndescription: ")
    rules = (proj.root / "CLAUDE.md").read_text(encoding="utf-8")
    assert rules.startswith("# Project\n\nOur own notes.\n\n<!-- vouch -->")
    settings = json.loads((proj.root / ".claude/settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
    assert settings["hooks"]["PostToolUse"] == [
        {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command",
                                                       "command": "vouch hook claude"}]}]
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"] == "vouch hook stop"
    capsys.readouterr()
    assert cli.main(["init", "--agents", "--yes"]) == 0
    assert "agents: already set up" in capsys.readouterr().out
    # the rules block is replaced in place, not appended twice
    from vouch.agents import planned
    (proj.root / "CLAUDE.md").write_text(rules.replace("Never type", "NEVER type"), encoding="utf-8")
    new = planned(proj.root, ["rules"])[proj.root / "CLAUDE.md"][1]
    assert new.count("<!-- vouch -->") == 1 and "Never type an empirical" in new


def test_public_names_stay_functions():
    import vouch
    import vouch.derived  # noqa: F401
    import vouch.tracked  # noqa: F401
    assert callable(vouch.track) and not isinstance(vouch.track, types.ModuleType)
    assert callable(vouch.derive) and not isinstance(vouch.derive, types.ModuleType)
    assert vouch.Stat.of([1.0, 3.0]).mean == 2.0 and math.isclose(vouch.gt(2, 1).margin, 1.0)
    with pytest.raises(AttributeError):
        vouch.nope


# ---------------------------------------------------------------------------
# the vouch/1 JSON envelope
# ---------------------------------------------------------------------------

def _validate(value, schema, defs, path="$"):
    """Enough of JSON Schema for vouch/1: const, enum, type, required, properties, items, $ref."""
    if "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[1]]
    if "const" in schema:
        assert value == schema["const"], path
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: {value!r}"
    kinds = schema.get("type")
    if kinds:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        py = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int,
              "null": type(None)}
        assert any(isinstance(value, py[k]) and not (k == "integer" and isinstance(value, bool))
                   for k in kinds), f"{path}: {value!r} is not {kinds}"
    for k in schema.get("required", []):
        assert k in value, f"{path}: missing {k}"
    for k, sub in (schema.get("properties") or {}).items():
        if isinstance(value, dict) and k in value:
            _validate(value[k], sub, defs, f"{path}.{k}")
    if "items" in schema and isinstance(value, list):
        for i, item in enumerate(value):
            _validate(item, schema["items"], defs, f"{path}[{i}]")


def test_json_output_follows_the_schema(proj, capsys):
    from pathlib import Path

    import vouch
    schema = json.loads((Path(vouch.__file__).parent / "schema" / "v1.json").read_text())
    proj.write("vouch_values.py", "import vouch\nvouch.expect('x.y', desc='d', producer='make')\n")
    paper(proj, "Typed 0.123, owed \\vouch{x.y}, unknown \\vouch{nope}, cited \\vouch{cifar.n_test}.")
    cli.main(["build"])
    capsys.readouterr()
    for args in (["check", "--json"], ["check", "--strict", "--json"], ["search", "vit", "--json"],
                 ["cite", "cifar.n_test", "--json"], ["todo", "--json"], ["suggest", "--json"],
                 ["compare", "evaluate.cifar.resnet.acc", "evaluate.cifar.vit.acc", "--json"],
                 ["ls", "--json"], ["status", "--json"], ["changes", "--json"]):
        cli.main(args)
        out = json.loads(capsys.readouterr().out)
        _validate(out, schema, schema["$defs"])
        assert out["command"] == args[0]
    cli.main(["check", "--strict", "--json"])
    out = json.loads(capsys.readouterr().out)
    checks = [i["check"] for i in out["issues"]]
    assert checks.index("unknown-key") < checks.index("pending") < checks.index("no-source")
