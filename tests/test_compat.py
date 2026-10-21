"""vouch runs on every Python that pyproject's `requires-python` admits (>= 3.10).

Python 3.12 (PEP 701) relaxed what an f-string's replacement field may contain: a
backslash, a comment, or the f-string's own quote character. Code written on 3.12+ can
use them without noticing, and it then fails to *parse* on 3.10 and 3.11. That is how
`tex/lint.py` came to carry `rf"...{r'\\d{2,}'}..."`, a SyntaxError on 3.10/3.11 in every
release from 0.1 to 0.4, so that build, check and the edit hook could not start there.

Under an old Python, compiling every module catches it. Under 3.12+, which the suite
usually runs on, the tokenizer exposes the replacement fields, and this test looks for
the three constructs directly.
"""

import sys
import tokenize
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "vouch"
FILES = sorted(SRC.rglob("*.py"))


def test_there_are_files_to_check():
    assert len(FILES) > 20


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_every_module_compiles(path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


def _pep701_only(path: Path) -> list[str]:
    """Replacement-field content that parses only on Python >= 3.12."""
    found = []
    stack = []                   # (quote of the enclosing f-string, brace depth inside it)
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            name = tokenize.tok_name[tok.type]
            if name == "FSTRING_START":
                if stack and stack[-1][1] > 0:
                    quote = tok.string.lstrip("rRfFbBuU")
                    if quote[0] == stack[-1][0][0]:
                        found.append(f"{tok.start[0]}: nested f-string reuses the quote {quote}")
                stack.append([tok.string.lstrip("rRfFbBuU"), 0])
                continue
            if not stack:
                continue
            if name == "FSTRING_END":
                stack.pop()
                continue
            if name == "FSTRING_MIDDLE":
                continue
            if tok.string == "{":
                stack[-1][1] += 1
            elif tok.string == "}":
                stack[-1][1] -= 1
            if stack[-1][1] <= 0:
                continue
            if name == "COMMENT":
                found.append(f"{tok.start[0]}: a comment inside a replacement field")
            elif "\\" in tok.string:
                found.append(f"{tok.start[0]}: a backslash inside a replacement field: "
                             f"{tok.string!r}")
            elif name == "STRING" and tok.string.lstrip("rRbBuU")[:1] == stack[-1][0][:1]:
                found.append(f"{tok.start[0]}: a string reusing the f-string's quote: "
                             f"{tok.string!r}")
    return found


@pytest.mark.skipif(sys.version_info < (3, 12), reason="older Pythons tokenize f-strings whole; "
                    "test_every_module_compiles covers them")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_no_fstring_syntax_newer_than_python_3_10(path):
    assert _pep701_only(path) == []
