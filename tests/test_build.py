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

def test_build_writes_values_and_tables(example):
    res = build(Config.load(example))
    written = {p.relative_to(example).as_posix() for p in res.written}
    # the provenance CSV is on demand (vouch export) unless [[paper]] asks for it
    assert written == {"paper/vouch-values.tex", "paper/vouch-tables/main.tex"}
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


def export_rows(example, capsys) -> list[dict]:
    capsys.readouterr()
    assert cli.main(["export", "--root", str(example)]) == 0
    return list(csv.DictReader(io.StringIO(capsys.readouterr().out)))


def test_opt_in_provenance_csv_is_written_and_checked(example, capsys):
    cfg_path = example / "vouch.toml"
    cfg_path.write_text(cfg_path.read_text(encoding="utf-8").replace(
        'main = "paper/main.tex"', 'main = "paper/main.tex"\nprovenance_csv = "paper/numbers.csv"'),
        encoding="utf-8")
    res = build(Config.load(example))
    assert (example / "paper/numbers.csv") in res.written
    text = (example / "paper/numbers.csv").read_text(encoding="utf-8")
    assert text.startswith("key,kind,rendered,")
    (example / "paper/numbers.csv").write_text(text.replace("80.2", "85.0"), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["check", "--root", str(example), "--no-env", "--quiet"]) == 1
    assert "paper/numbers.csv does not match" in capsys.readouterr().out


def test_provenance_csv_rows_in_reading_order(example, capsys):
    build(Config.load(example))
    rows = export_rows(example, capsys)
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
# LaTeX, for real: every provenance mode
# ---------------------------------------------------------------------------

def pdf_blobs(pdf: Path) -> list[bytes]:
    """The PDF's bytes plus every decompressed stream (object streams hold annotations)."""
    data = pdf.read_bytes()
    blobs = [data]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            blobs.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return blobs


def pdf_strings(pdf: Path, key: bytes) -> list[str]:
    """Every PDF string value of dictionary entry ``key`` (e.g. b"/TU", b"/Contents", b"/D")."""
    out = []
    for blob in pdf_blobs(pdf):
        for m in re.finditer(re.escape(key) + rb"\s*\(((?:\\.|[^\\)])*)\)", blob):
            s = re.sub(rb"\\([0-7]{3})", lambda o: bytes([int(o.group(1), 8)]), m.group(1))
            s = re.sub(rb"\\(.)", rb"\1", s, flags=re.S)
            # hyperref writes Unicode strings as UTF-16BE with a byte-order mark
            out.append(s[2:].decode("utf-16-be", errors="replace") if s.startswith(b"\xfe\xff")
                       else s.decode("latin-1"))
    return out


def tooltips(pdf: Path) -> list[str]:
    return pdf_strings(pdf, b"/TU")


def pdflatex(paper_dir: Path, main: str = "main.tex", runs: int = 1) -> subprocess.CompletedProcess:
    proc = None
    for _ in range(runs):
        proc = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", main],
                              cwd=paper_dir, capture_output=True, text=True, errors="replace")
    return proc


def variant(paper: Path, options: str, name: str, extra: str = "") -> str:
    """main.tex with \\usepackage[options]{vouch}, saved as name.tex."""
    src = (paper / "main.tex").read_text(encoding="utf-8")
    src = src.replace(r"\usepackage{vouch}", r"\usepackage[" + options + "]{vouch}")
    if extra:
        src = src.replace(r"\end{document}", extra + "\n" + r"\end{document}")
    (paper / f"{name}.tex").write_text(src, encoding="utf-8")
    return f"{name}.tex"


def log_of(paper: Path, name: str) -> str:
    return (paper / f"{name}.log").read_text(errors="replace")


needs_latex = pytest.mark.skipif(not HAVE_LATEX, reason="pdflatex not installed")


@needs_latex
def test_link_mode_is_the_default_and_links_to_the_appendix(example):
    build(Config.load(example))
    paper = example / "paper"
    proc = pdflatex(paper, runs=2)                  # page numbers need the second pass
    log = log_of(paper, "main")
    assert proc.returncode == 0, log[-3000:]
    assert "Package vouch Warning" not in log and "Overfull" not in log
    dests = set(pdf_strings(paper / "main.pdf", b"/D"))
    # every cited value links to its appendix entry, and entries link back to citations
    assert {"vouch.toy.centroid.acc", "vouch.toy.centroid_beats_majority",
            "vouch.main.centroid.acc"} <= dests
    assert any(d.startswith("vouch@a@") for d in dests)
    assert tooltips(paper / "main.pdf") == []
    aux = (paper / "main.aux").read_text(errors="replace")
    assert r"\vouch@pageof{1}{1}" in aux


@needs_latex
def test_tooltip_mode_has_provenance_tooltips(example):
    build(Config.load(example))
    paper = example / "paper"
    proc = pdflatex(paper, variant(paper, "provenance=tooltip", "tip"))
    log = log_of(paper, "tip")
    assert proc.returncode == 0, log[-3000:]
    assert "Package vouch Warning" not in log and "Overfull" not in log
    tips = tooltips(paper / "tip.pdf")
    assert any(t.startswith("toy.centroid.acc = 0.802 +/- ") and "run train | train.py:25" in t
               for t in tips)
    assert any(t.startswith("claim toy.centroid_beats_majority: HOLDS") for t in tips)
    assert any(t.startswith("main.centroid.acc = ") for t in tips)      # table cells too


@needs_latex
def test_note_mode_has_sticky_notes(example):
    build(Config.load(example))
    paper = example / "paper"
    assert pdflatex(paper, variant(paper, "provenance=note", "note")).returncode == 0
    notes = pdf_strings(paper / "note.pdf", b"/Contents")
    assert any(n.startswith("toy.centroid.acc = 0.802") for n in notes)


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
    tip = variant(paper, "provenance=tooltip", "tip")
    proc = pdflatex(paper, tip)
    assert proc.returncode == 0, log_of(paper, "tip")[-2000:]
    tips = tooltips(paper / "tip.pdf")
    changed = [t for t in tips if t.startswith("toy.centroid.acc = ")]
    assert changed and "CHANGED: was 80.2 +/- 3.5% (acked" in changed[0]
    assert "state: fresh" in changed[0]
    # the appendix says so too
    values = (paper / "vouch-values.tex").read_text(encoding="utf-8")
    prov = next(ln for ln in values.splitlines() if ln.startswith(r"\vouch@prov{toy.centroid.acc}"))
    assert r"\textcolor{vouchchanged}{CHANGED: was 80.2 +/- 3.5\% (acked" in prov
    # the working tree moves on: the next build says the run is stale
    edit(example / "models.py", "abs(x[0] - cent[y][0])", "abs(x[0] - cent[y][0]) * 1.0")
    build(Config.load(example), notify=False)
    assert pdflatex(paper, tip).returncode == 0
    tips = tooltips(paper / "tip.pdf")
    assert any("STATE: STALE - models.py::nearest_centroid changed" in t for t in tips)


@needs_latex
def test_final_has_no_links_tooltips_or_appendix_and_refuses_unknown_keys(example):
    build(Config.load(example))
    paper = example / "paper"
    assert pdflatex(paper, variant(paper, "final", "final")).returncode == 0
    assert tooltips(paper / "final.pdf") == []
    assert not [d for d in pdf_strings(paper / "final.pdf", b"/D") if d.startswith("vouch")]

    proc = pdflatex(paper, variant(paper, "final", "bad", r"\vouch{no.such.key}"))
    assert proc.returncode != 0
    assert "Value `no.such.key' is undefined" in log_of(paper, "bad")


@needs_latex
def test_draft_marks_unknown_keys_and_false_claims(example):
    rec = example / ".vouch" / "runs" / "train.json"
    data = json.loads(rec.read_text(encoding="utf-8"))
    from vouch.store import write_record
    data["claims"]["toy.centroid_beats_majority"]["holds"] = False
    write_record(example / ".vouch", data)            # a legitimately re-sealed record
    build(Config.load(example))
    paper = example / "paper"
    proc = pdflatex(paper, variant(paper, "provenance=link", "marks", r"\vouch{no.such}"))
    log = log_of(paper, "marks")
    assert proc.returncode == 0
    assert "Value `no.such' undefined" in log
    assert "Claim `toy.centroid_beats_majority' is FALSE" in log
    assert "There were undefined or pending vouch values" in log


@needs_latex
def test_hard_cases_compile_in_every_mode(example):
    """TOC and list-of-figures lines (already links), amsmath labels, footnotes, claims
    holding values, cleveref -- in every provenance mode."""
    build(Config.load(example))
    paper = example / "paper"
    stress = "\n".join([
        r"\documentclass{article}", r"\usepackage{amsmath}", r"\usepackage{vouch}",
        r"\usepackage[colorlinks]{hyperref}", r"\usepackage{cleveref}", r"\begin{document}",
        r"\tableofcontents\listoffigures",
        r"\section{Accuracy \vouch{toy.centroid.acc.mean}}\label{sec:acc}",
        r"See \cref{sec:acc} and \cref{eq:a}.",
        r"\begin{align} a &= \vouch{toy.centroid.acc.mean} \label{eq:a}\\",
        r" b &= \vouch{toy.majority.acc.mean} + \vouch{train.param.seeds} \label{eq:b}\end{align}",
        r"\vouchclaim{toy.centroid_beats_majority}{Centroid beats majority, spanning",
        r"\vouch{toy.centroid.acc} against \vouch{toy.majority.acc}, a claim long enough",
        r"to force a line break inside its link}.\footnote{Note \vouch{toy.centroid.n_test}.}",
        r"\begin{figure}[h]\centering\fbox{x}\caption{Caption \vouch{toy.majority.acc}}\end{figure}",
        r"Equation \eqref{eq:b} holds.", r"\end{document}", ""])
    (paper / "stress.tex").write_text(stress, encoding="utf-8")
    for mode in ("provenance=link", "provenance=tooltip", "provenance=note",
                 "provenance=off", "final"):
        name = "stress_" + mode.split("=")[-1]
        src = stress.replace(r"\usepackage{vouch}", r"\usepackage[" + mode + "]{vouch}")
        (paper / f"{name}.tex").write_text(src, encoding="utf-8")
        proc = pdflatex(paper, f"{name}.tex", runs=2)
        log = log_of(paper, name)
        assert proc.returncode == 0, (mode, log[-2000:])
        assert "multiply defined" not in log and "Overfull" not in log, mode
        assert "Package vouch Warning" not in log, mode

