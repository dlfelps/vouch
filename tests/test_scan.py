from pathlib import Path

from vouch.tex.scan import mask_comments, scan, sentence_at


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.lstrip("\n"), encoding="utf-8", newline="\n")
    return p


def keys(doc, kind=None):
    return [(c.kind, c.key, c.fmt) for c in doc.citations if kind is None or c.kind == kind]


def test_mask_comments_respects_escapes():
    src = r"a 50\% b % comment \vouch{hidden}" "\n" r"c \\% gone \vouch{also}" "\n" r"\\\% kept"
    masked = mask_comments(src)
    assert len(masked) == len(src)
    assert r"50\% b " in masked and "hidden" not in masked
    assert r"c \\" in masked and "also" not in masked
    assert r"\\\% kept" in masked


def test_reading_order_across_inputs_and_optional_formats(tmp_path):
    write(tmp_path, "paper/main.tex", r"""
\documentclass{article}
\usepackage{vouch}
\begin{document}
First \vouch{a.one}.
\input{sections/two}
Then \vouch [.2f] {a.three} and \vouchraw{a.raw}.
% \vouch{commented.out}
\include{sections/four}
\end{document}
""")
    write(tmp_path, "paper/sections/two.tex", r"Two: \vouchclaim{c.beats}{beats by \vouch{a.gain}}." "\n")
    write(tmp_path, "paper/sections/four.tex", r"\vouchtable{main}" "\n")
    doc = scan(tmp_path / "paper/main.tex", tmp_path)
    assert keys(doc) == [("value", "a.one", None), ("claim", "c.beats", None),
                         ("value", "a.gain", None), ("value", "a.three", ".2f"),
                         ("raw", "a.raw", None), ("table", "main", None)]
    two = doc.citations[1]
    assert two.file == "paper/sections/two.tex" and two.line == 1
    assert doc.citations[3].line == 6 and doc.citations[3].file == "paper/main.tex"


def test_short_aliases_only_with_option(tmp_path):
    body = r"\begin{document}\val{x} \vclaim{y}{z} \vtable{t}\end{document}"
    write(tmp_path, "a.tex", r"\usepackage{vouch}" + body)
    write(tmp_path, "b.tex", r"\usepackage[final, short]{vouch}" + body)
    assert keys(scan(tmp_path / "a.tex", tmp_path)) == []
    assert keys(scan(tmp_path / "b.tex", tmp_path)) == [("value", "x", None), ("claim", "y", None),
                                                        ("table", "t", None)]


def test_macro_definitions_count_only_where_used(tmp_path):
    write(tmp_path, "main.tex", r"""
\newcommand{\acc}{\vouch{cifar.acc}}
\newcommand\unused{\vouch{never.shown}}
\def\gain#1{\vouch[.1f]{cifar.gain}#1}
\newcommand{\both}{\acc{} and \gain{}}
\begin{document}
We get \acc. Again \acc.
Combined: \both.
\end{document}
""")
    doc = scan(tmp_path / "main.tex", tmp_path)
    got = [(c.key, c.fmt, c.line, c.via) for c in doc.citations]
    assert got == [("cifar.acc", None, 6, "acc"), ("cifar.acc", None, 6, "acc"),
                   ("cifar.acc", None, 7, "both"), ("cifar.gain", ".1f", 7, "both")]
    assert "never.shown" not in {c.key for c in doc.citations}


def test_verbatim_is_skipped(tmp_path):
    write(tmp_path, "main.tex", r"""
\begin{document}
\begin{verbatim}
\vouch{in.verbatim}
\end{verbatim}
\verb|\vouch{in.verb}| and \vouch{real}
\end{document}
""")
    assert keys(scan(tmp_path / "main.tex", tmp_path)) == [("value", "real", None)]


def test_figures_resolve_through_graphicspath(tmp_path):
    write(tmp_path, "paper/figs/curve.pdf", "%PDF")
    write(tmp_path, "paper/main.tex", r"""
\graphicspath{{figs/}}
\begin{document}
\includegraphics[width=0.5\linewidth]{curve}
\includegraphics{missing}
\end{document}
""")
    doc = scan(tmp_path / "paper/main.tex", tmp_path)
    figs = [(c.key, c.resolved) for c in doc.citations if c.kind == "figure"]
    assert figs == [("paper/figs/curve.pdf", True), ("missing", False)]


def test_missing_inputs_reported(tmp_path):
    write(tmp_path, "main.tex", r"\begin{document}\input{nope}\end{document}")
    doc = scan(tmp_path / "main.tex", tmp_path)
    assert doc.missing_inputs == [("main.tex", 1, "nope")]


def test_include_cycles_terminate(tmp_path):
    write(tmp_path, "main.tex", r"\input{a} \vouch{m}")
    write(tmp_path, "a.tex", r"\input{main} \vouch{a}")
    doc = scan(tmp_path / "main.tex", tmp_path)
    assert [c.key for c in doc.citations] == ["a", "m"]


def test_sentence_extraction():
    text = ("Setup is standard. ResNet-50 reaches \\vouch{cifar.acc} top-1 accuracy, "
            "the best of all models. Next sentence.")
    off = text.index("\\vouch")
    assert sentence_at(text, off) == ("ResNet-50 reaches \\vouch{cifar.acc} top-1 accuracy, "
                                      "the best of all models.")
    para = "First para.\n\nSecond para with \\vouch{x}\nand no full stop\n\nThird."
    assert sentence_at(para, para.index("\\vouch")) == "Second para with \\vouch{x} and no full stop"
    long = "word " * 200 + "\\vouch{k} " + "word " * 200 + "."
    s = sentence_at(long, long.index("\\vouch"), limit=60)
    assert "\\vouch{k}" in s and len(s) <= 62
