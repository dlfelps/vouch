"""Shared fixtures.

Recording is tested by running real scripts in throwaway projects: the implicit
run finalizes at interpreter exit and atomicity is about process death, neither of
which can be exercised honestly inside the pytest process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


class Project:
    def __init__(self, root: Path):
        self.root = root

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8", newline="\n")
        return p

    def run(self, script: str, *args: str, env: dict | None = None,
            check: bool = False) -> subprocess.CompletedProcess:
        e = {k: v for k, v in os.environ.items() if not k.startswith("VOUCH_")}
        e["PYTHONIOENCODING"] = "utf-8"
        e.update(env or {})
        proc = subprocess.run([sys.executable, script, *args], cwd=self.root, env=e,
                              capture_output=True, text=True, encoding="utf-8")
        if check and proc.returncode != 0:
            raise AssertionError(f"{script} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
        return proc

    def record(self, run_id: str) -> dict:
        return json.loads((self.root / ".vouch" / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))

    def has_record(self, run_id: str) -> bool:
        return (self.root / ".vouch" / "runs" / f"{run_id}.json").exists()


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "vouch.toml").write_text("", encoding="utf-8")
    return Project(root)


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "minimal"


@pytest.fixture
def example(tmp_path: Path) -> Path:
    """examples/minimal copied fresh, its experiment run, and `vouch init` done."""
    import shutil

    from vouch import cli
    root = tmp_path / "ex"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        ".vouch", "*.pdf", "*.aux", "*.log", "*.out", "*.fls", "*.fdb_latexmk", "*.up?",
        "vouch-*", "vouch.sty", ".gitattributes"))
    subprocess.run([sys.executable, "train.py"], cwd=root, check=True, capture_output=True)
    assert cli.main(["init", "--root", str(root)]) == 0
    return root


def rerun(root: Path, script: str = "train.py") -> None:
    subprocess.run([sys.executable, script], cwd=root, check=True, capture_output=True)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
