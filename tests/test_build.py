"""vouch build / init / export, end to end on examples/minimal -- through to a PDF."""

import csv
import io
import json
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from vouch import cli
from vouch.build import build
from vouch.config import Config

HAVE_LATEX = shutil.which("pdflatex") is not None


def values(root: Path) -> str:
    return (root / "paper" / "vouch-values.tex").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# generated files
# ---------------------------------------------------------------------------

def test_build_writes_values_tables_and_csv(example):
    res = build(Config.load(example))
    written = {p.relative_to(example).as_posix() for p in res.written}
    assert written == {"paper/vouch-values.tex", "paper/vouch-provenance.csv",
                       "paper/vouch-tables/main.tex"}
    v = values(example)
    # default rendering from [metrics] (.1pct), Stat as mean \pm std
    assert r"\vouch@set{toy.centroid.acc}{}{\ensuremath{80.2 \pm 3.5}\%}" in v
    # the cited override is rendered too
    assert r"\vouch@set{toy.centroid.acc.mean}{.3f}{0.802}{0.802}" in v
    assert r"\vouch@claim{toy.centroid_beats_majority}{1}{claim toy.centroid\_beats\_majority: HOLDS" in v
    assert r"\vouch@table{main}{vouch-tables/main.tex}" in v
    assert r"\vouch@raw{train.param.seeds}{5}" in v
    tooltip = re.search(r"\\vouch@set\{toy\.centroid\.acc\}\{\}\{[^\n]*?\}\{[^\n]*?\}\{(.*)\}\{0\}", v)
    assert tooltip and r"run train | train.py:25\textCR python train.py" in tooltip.group(1)


def test_table_body_marks_the_best_cell(example):
    build(Config.load(example))
    body = (example / "paper" / "vouch-tables" / "main.tex").read_text(encoding="utf-8")
    lines = [ln for ln in body.splitlines() if not ln.startswith("%")]
    assert lines == [r"\vouch{main.centroid.model} & \vouchbest{\vouch{main.centroid.acc}} \\",
                     r"\vouch{main.majority.model} & \vouch{main.majority.acc} \\"]


def test_rebuild_is_byte_identical_and_writes_nothing(example):
    build(Config.load(example))
    first = values(example)
    res = build(Config.load(example))
    assert res.written == [] and values(example) == first


def test_provenance_csv_rows_in_reading_order(example):
    build(Config.load(example))
    text = (example / "paper" / "vouch-provenance.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["key"] for r in rows[:6]] == [
        "toy.centroid.acc.mean", "toy.centroid.acc", "toy.majority.acc",
        "toy.centroid_beats_majority", "toy.centroid.n_test", "train.param.seeds"]
    first = rows[0]
    assert first["experiment"] == "train" and first["script"] == "train.py"
    assert first["rendered"] == r"80.2\% | 0.802" and first["fmt"] == ".1pct | .3f"
    assert first["cited_at"] == "paper/main.tex:13; paper/main.tex:16; paper/main.tex:22"
    claim = next(r for r in rows if r["kind"] == "claim")
    assert claim["rendered"] == "HOLDS" and claim["raw_value"].startswith("true ")
    cells = [r["key"] for r in rows if r["kind"] == "table-cell"]
    assert cells == ["main.centroid.model", "main.centroid.acc", "main.majority.model",
                     "main.majority.acc"]


def test_unknown_keys_and_bad_formats_are_reported(example):
    main = example / "paper" / "main.tex"
    src = main.read_text(encoding="utf-8")
    main.write_text(src.replace(r"\end{document}",
                                r"\vouch{toy.centroid.accc} \vouch[.1pct]{main.centroid.model}"
                                "\n" r"\end{document}"), encoding="utf-8")
    line = main.read_text(encoding="utf-8").splitlines().index(
        r"\vouch{toy.centroid.accc} \vouch[.1pct]{main.centroid.model}") + 1
    res = build(Config.load(example))
    unknown = [i for i in res.plans[0].issues if i.check == "unknown-key"]
    assert len(unknown) == 1 and "toy.centroid.accc" in unknown[0].message
    assert "did you mean toy.centroid.acc" in unknown[0].message
    assert (unknown[0].file, unknown[0].line) == ("paper/main.tex", line)
    fmt = [i for i in res.plans[0].issues if i.check == "format"]
    assert len(fmt) == 1 and fmt[0].severity == "error" and fmt[0].line == line
    assert "main.centroid.model: format '.1pct' cannot render str" in fmt[0].message


def test_store_edits_are_reported(example):
    rec = example / ".vouch" / "runs" / "train.json"
    rec.write_text(rec.read_text(encoding="utf-8").replace('"n_test": ', '"n_test": ', 1)
                   .replace("200", "201", 1), encoding="utf-8")
    res = build(Config.load(example))
    assert any(i.check == "store-edited" for i in res.issues)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_build_json(example, capsys):
    assert cli.main(["build", "--root", str(example), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "vouch/1" and out["papers"][0]["counts"]["claims"] == 1


def test_cli_export_all_to_stdout(example, capsys):
    assert cli.main(["export", "--root", str(example), "--all"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    keys = {r["key"] for r in rows}
    assert "toy.majority.n_test" in keys and "train.param.n_train" in keys   # uncited, via --all


def test_cli_build_without_paper_is_unverified(tmp_path, capsys):
    (tmp_path / "vouch.toml").write_text("", encoding="utf-8")
    assert cli.main(["build", "--root", str(tmp_path)]) == 2
    assert "no [[paper]]" in capsys.readouterr().err


def test_init_detects_the_paper(tmp_path, capsys):
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    (tmp_path / "paper" / "intro.tex").write_text("no class here\n", encoding="utf-8")
    assert cli.main(["init", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert 'main = "paper/main.tex"' in (tmp_path / "vouch.toml").read_text(encoding="utf-8")
    assert (tmp_path / "paper" / "vouch.sty").exists()
    assert "\\usepackage{vouch}" in out
    assert (tmp_path / ".vouch" / ".gitignore").read_text() == "cache/\n"


def test_init_with_several_papers_asks(tmp_path, capsys):
    for name in ("a.tex", "b.tex"):
        (tmp_path / name).write_text("\\documentclass{article}\n", encoding="utf-8")
    assert cli.main(["init", "--root", str(tmp_path)]) == 2
    assert "several papers found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# LaTeX, for real
# ---------------------------------------------------------------------------

def tooltips(pdf: Path) -> list[str]:
    """The /TU strings of every widget annotation, compressed object streams included."""
    data = pdf.read_bytes()
    blobs = [data]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            blobs.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    out = []
    for blob in blobs:
        for m in re.finditer(rb"/TU\s*\(((?:\\.|[^\\)])*)\)", blob):
            s = re.sub(rb"\\([0-7]{3})", lambda o: bytes([int(o.group(1), 8)]), m.group(1))
            out.append(s.decode("latin-1").replace("\\", ""))
    return out


def pdflatex(paper_dir: Path, main: str = "main.tex") -> subprocess.CompletedProcess:
    return subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", main],
                          cwd=paper_dir, capture_output=True, text=True, errors="replace")


needs_latex = pytest.mark.skipif(not HAVE_LATEX, reason="pdflatex not installed")


@needs_latex
def test_pdf_has_values_and_provenance_tooltips(example):
    build(Config.load(example))
    paper = example / "paper"
    proc = pdflatex(paper)
    log = (paper / "main.log").read_text(errors="replace")
    assert proc.returncode == 0, log[-3000:]
    assert "Package vouch Warning" not in log
    assert "Overfull" not in log
    tips = tooltips(paper / "main.pdf")
    assert any(t.startswith("toy.centroid.acc = 0.802 +/- ") and "run train | train.py:25" in t
               for t in tips)
    assert any(t.startswith("claim toy.centroid_beats_majority: HOLDS") for t in tips)
    assert any(t.startswith("main.centroid.acc = ") for t in tips)      # table cells too


@needs_latex
def test_pdf_shows_changes_and_run_state(example):
    from conftest import edit, rerun
    build(Config.load(example))
    edit(example / "models.py",
         "        return min(cent, key=lambda y: (x[0] - cent[y][0]) ** 2 + (x[1] - cent[y][1]) ** 2)",
         "        return min(cent, key=lambda y: abs(x[0] - cent[y][0]) + abs(x[1] - cent[y][1]))")
    rerun(example)
    build(Config.load(example), notify=False)
    paper = example / "paper"
    proc = pdflatex(paper)
    assert proc.returncode == 0, (paper / "main.log").read_text(errors="replace")[-2000:]
    tips = tooltips(paper / "main.pdf")
    changed = [t for t in tips if t.startswith("toy.centroid.acc = ")]
    assert changed and "CHANGED: was 80.2 +/- 3.5% (acked" in changed[0]
    assert "state: fresh" in changed[0]
    # the working tree moves on: the next build's tooltips say the run is stale
    edit(example / "models.py", "abs(x[0] - cent[y][0])", "abs(x[0] - cent[y][0]) * 1.0")
    build(Config.load(example), notify=False)
    assert pdflatex(paper).returncode == 0
    tips = tooltips(paper / "main.pdf")
    assert any("STATE: STALE - models.py::nearest_centroid changed" in t for t in tips)


@needs_latex
def test_final_has_no_tooltips_and_refuses_unknown_keys(example):
    build(Config.load(example))
    paper = example / "paper"
    src = (paper / "main.tex").read_text(encoding="utf-8")
    (paper / "final.tex").write_text(src.replace(r"\usepackage{vouch}", r"\usepackage[final]{vouch}"),
                                     encoding="utf-8")
    assert pdflatex(paper, "final.tex").returncode == 0
    assert tooltips(paper / "final.pdf") == []

    (paper / "bad.tex").write_text(src.replace(r"\usepackage{vouch}", r"\usepackage[final]{vouch}")
                                   .replace(r"\end{document}", r"\vouch{no.such.key}\end{document}"),
                                   encoding="utf-8")
    proc = pdflatex(paper, "bad.tex")
    assert proc.returncode != 0
    assert "Value `no.such.key' is undefined" in (paper / "bad.log").read_text(errors="replace")


@needs_latex
def test_draft_marks_unknown_keys_and_false_claims(example):
    rec = example / ".vouch" / "runs" / "train.json"
    data = json.loads(rec.read_text(encoding="utf-8"))
    from vouch.store import write_record
    data["claims"]["toy.centroid_beats_majority"]["holds"] = False
    write_record(example / ".vouch", data)            # a legitimately re-sealed record
    build(Config.load(example))
    paper = example / "paper"
    src = (paper / "main.tex").read_text(encoding="utf-8")
    (paper / "main.tex").write_text(src.replace(r"\end{document}", r"\vouch{no.such}\end{document}"),
                                    encoding="utf-8")
    proc = pdflatex(paper)
    log = (paper / "main.log").read_text(errors="replace")
    assert proc.returncode == 0
    assert "Value `no.such' undefined" in log
    assert "Claim `toy.centroid_beats_majority' is FALSE" in log
    assert "There were undefined or pending vouch values" in log
