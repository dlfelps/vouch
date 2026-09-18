"""Git pre-commit hook installation (SPEC §15).

The hook runs ``vouch check`` -- never an experiment -- so it stays fast enough that
nobody reaches for ``--no-verify`` (asqc's lesson: a slow hook is worse than none).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

MARKER = "# vouch pre-commit hook"

HOOK = """#!/bin/sh
{marker} (installed by `vouch hook install`; edit freely, or delete to remove)
# Checks the paper's numbers against the recorded runs. Never runs experiments.
if [ -x ./.venv/Scripts/vouch.exe ]; then V=./.venv/Scripts/vouch.exe
elif [ -x ./.venv/bin/vouch ]; then V=./.venv/bin/vouch
else V=vouch
fi
exec "$V" check --quiet{strict}
"""


class HookError(RuntimeError):
    pass


def hooks_dir(root: Path) -> Path:
    try:
        r = subprocess.run(["git", "-C", str(root), "config", "core.hooksPath"],
                           capture_output=True, text=True, timeout=10)
        custom = r.stdout.strip() if r.returncode == 0 else ""
        if custom:
            p = Path(custom)
            return p if p.is_absolute() else root / p
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HookError(f"git is not available: {exc}") from None
    if r.returncode != 0:
        raise HookError(f"{root} is not inside a git repository")
    git_dir = Path(r.stdout.strip())
    return (git_dir if git_dir.is_absolute() else root / git_dir) / "hooks"


def install(root: Path, strict: bool = False, force: bool = False) -> Path:
    d = hooks_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "pre-commit"
    if path.exists() and MARKER not in path.read_text(encoding="utf-8", errors="replace") and not force:
        raise HookError(f"{path} already exists and is not vouch's. Add this line to it:\n\n"
                        f"  vouch check --quiet{' --strict' if strict else ''}\n\n"
                        f"or re-run with --force to replace it.")
    path.write_text(HOOK.format(marker=MARKER, strict=" --strict" if strict else ""),
                    encoding="utf-8", newline="\n")
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
