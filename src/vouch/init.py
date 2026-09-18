"""``vouch init``: set a project up in one command (SPEC §12)."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from .config import CONFIG_NAME, Config
from .store import ensure_store

PREFERRED_MAIN = ("main.tex", "paper.tex", "manuscript.tex", "ms.tex", "article.tex")
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "build", "dist", ".vouch",
             "__pycache__", ".tox"}

TOML_TEMPLATE = """\
# vouch.toml -- see SPEC.md section 3.3. Every setting has a default.

[[paper]]
main = "{main}"

# Project-wide defaults for metric keys, matched by glob. {{0}}, {{1}}, {{-1}} are key
# segments, so cifar.resnet.acc gets "top-1 test accuracy, resnet on cifar".
# [metrics]
# "*.acc"  = {{ fmt = ".1pct", better = "higher", desc = "top-1 test accuracy, {{1}} on {{0}}" }}
# "*.loss" = {{ fmt = ".3f",   better = "lower",  desc = "test loss, {{1}} on {{0}}" }}

# [format]
# rounding = "half_up"         # or "half_even"
# default_float = ".3g"
# siunitx = false
# named = {{ pct1 = ".1pct" }}

# [freshness]
# granularity = "function"     # or "module"
"""


class InitError(RuntimeError):
    pass


def packaged_sty() -> str:
    return resources.files("vouch").joinpath("data", "vouch.sty").read_text(encoding="utf-8")


def _has_documentclass(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return False
    return re.search(r"^[^%\n]*\\documentclass", head, re.M) is not None


def find_main(root: Path) -> Path:
    cands = []
    for p in sorted(root.rglob("*.tex")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if p.name.startswith("vouch-") or "vouch-tables" in p.parts:
            continue
        if _has_documentclass(p):
            cands.append(p)
    if len(cands) == 1:
        return cands[0]
    preferred = [p for p in cands if p.name in PREFERRED_MAIN]
    if len(preferred) == 1:
        return preferred[0]
    if not cands:
        raise InitError("no .tex file with \\documentclass found; pass --paper path/to/main.tex")
    listing = "\n  ".join(p.relative_to(root).as_posix() for p in cands)
    raise InitError(f"several papers found; choose one with --paper:\n  {listing}")


def init(root: Path, paper: str | None = None) -> list[str]:
    """Create vouch.toml, the store and vouch.sty. Returns human-readable notes."""
    root = root.resolve()
    notes: list[str] = []
    cfg_path = root / CONFIG_NAME
    if cfg_path.exists():
        cfg = Config.load(root)
        mains = [p.get("main") for p in (cfg.data.get("paper") or []) if p.get("main")]
        if paper and paper not in mains:
            raise InitError(f"{CONFIG_NAME} already exists; add the paper to it by hand:\n\n"
                            f"  [[paper]]\n  main = \"{paper}\"")
        main_paths = [root / m for m in mains]
        notes.append(f"{CONFIG_NAME} exists; left unchanged")
    else:
        main = (root / paper) if paper else find_main(root)
        if not main.is_file():
            raise InitError(f"{paper} does not exist")
        rel = main.resolve().relative_to(root).as_posix()
        cfg_path.write_text(TOML_TEMPLATE.format(main=rel), encoding="utf-8", newline="\n")
        notes.append(f"wrote {CONFIG_NAME} (paper: {rel})")
        main_paths = [main]

    ensure_store(root / ".vouch")
    sty = packaged_sty()
    for main in main_paths:
        target = main.parent / "vouch.sty"
        rel = target.resolve().relative_to(root).as_posix()
        if not target.exists():
            target.write_text(sty, encoding="utf-8", newline="\n")
            notes.append(f"copied vouch.sty to {rel}")
        elif target.read_text(encoding="utf-8") != sty:
            target.write_text(sty, encoding="utf-8", newline="\n")
            notes.append(f"updated {rel} to this version of vouch")
        src = main.read_text(encoding="utf-8", errors="replace") if main.is_file() else ""
        if not re.search(r"^[^%\n]*\\usepackage\s*(\[[^\]]*\])?\s*\{vouch\}", src, re.M):
            notes.append(f"add to the preamble of {main.resolve().relative_to(root).as_posix()}:"
                         f"  \\usepackage{{vouch}}")

    attrs = root / ".gitattributes"
    want = ["*/vouch-values.tex linguist-generated=true",
            "*/vouch-tables/*.tex linguist-generated=true"]
    have = attrs.read_text(encoding="utf-8").splitlines() if attrs.exists() else []
    missing = [w for w in want if w not in have]
    if missing:
        with open(attrs, "a", encoding="utf-8", newline="\n") as fh:
            if have and have[-1].strip():
                fh.write("\n")
            fh.write("# vouch: generated files\n" + "\n".join(missing) + "\n")
        notes.append("marked generated files in .gitattributes")
    return notes
