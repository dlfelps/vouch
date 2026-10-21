"""SPEC.md describes what exists: the API it names, the configuration it documents, the
checks it lists.

Five of the defects found in use were the SPEC or the docs promising what the code did
not do: `vouch.Pending` (f85bd76), `vouch check --paper` (52d2888), `--json` on every
command (12f7602), `vouch recompute` (8b4d4ec), and a placeholder the docs described two
ways (baa1ab0). test_cli_reference.py holds the command line to SPEC section 12; this
holds the rest of the reader-facing surface to the code, so the next promise that drifts
fails here instead of in someone's paper.
"""

import importlib.util
import re
from pathlib import Path

import vouch
from vouch.api import Run

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "SPEC.md").read_text(encoding="utf-8")
SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "vouch").rglob("*.py"))

FILES_NOT_NAMES = {"sty", "toml"}            # vouch.sty, vouch.toml: files, not attributes


def section(start: str, end: str) -> str:
    return SPEC[SPEC.index(start):SPEC.index(end)]


def test_every_vouch_name_the_spec_uses_exists():
    names = set(re.findall(r"\bvouch\.([A-Za-z_][A-Za-z_0-9]*)\b", SPEC)) - FILES_NOT_NAMES
    assert len(names) > 10, "the pattern finds no names: it is out of date"
    missing = []
    for n in sorted(names):
        if hasattr(vouch, n):
            continue
        try:
            if importlib.util.find_spec(f"vouch.{n}") is None:   # a module: python -m vouch.exec
                raise ImportError(n)
        except ImportError:
            missing.append(n)
    assert not missing, f"SPEC names vouch.{missing}, which vouch does not have"


def test_every_run_method_the_spec_shows_exists():
    methods = set(re.findall(r"\brun\.([a-z_]+)\(", SPEC))
    assert methods, "the pattern finds no run methods: it is out of date"
    assert not sorted(m for m in methods if not hasattr(Run, m))


def test_every_documented_config_key_is_read():
    block = re.search(r"```toml\n(.*?)```", section("### 3.3", "## 4."), re.S).group(1)
    keys, sec = [], None
    for line in block.splitlines():
        m = re.match(r"\s*\[\[?([a-z_.]+)\]\]?", line)
        if m:
            sec = m.group(1)
            continue
        m = re.match(r"\s*([a-z_]+)\s*=", line)
        if m and sec:
            keys.append(f"{sec}.{m.group(1)}")
    assert len(keys) > 20, "the pattern finds no keys: it is out of date"
    unread = [k for k in keys if f'"{k.rsplit(".", 1)[1]}"' not in SOURCE]
    assert not unread, f"SPEC 3.3 documents {unread}, which no code reads"


def test_every_listed_check_is_emitted():
    rows = re.findall(r"^\| `([a-z][a-z-]+)` \| (?:error|warning|info)",
                      section("## 11. Checks", "## 12."), re.M)
    assert len(rows) > 15, "the pattern finds no checks: it is out of date"
    never = [c for c in rows if f'"{c}"' not in SOURCE]
    assert not never, f"SPEC 11 lists {never}, which no code emits"
