"""Content hashes for inputs and artifacts (files and directories).

Code is hashed semantically (see ``units``); data is hashed by content. A
directory hashes as the sorted list of (relative path, file hash), so adding,
removing, renaming or editing any file inside it changes the hash.

``mode="stat"`` trades certainty for speed on very large inputs: it records size
and modification time only (SPEC §8.3).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

CHUNK = 1 << 20
SKIP_DIRS = {".git", "__pycache__", ".ipynb_checkpoints", ".DS_Store"}


def hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _stat_sig(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _walk_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in SKIP_DIRS:
                continue
            p = Path(dirpath) / name
            yield p.relative_to(root).as_posix(), p


def hash_path(path: str | os.PathLike, mode: str = "content") -> str | None:
    """Hash of a file or directory, or None if it doesn't exist."""
    p = Path(path)
    if mode not in ("content", "stat"):
        raise ValueError(f"unknown hashing mode {mode!r}")
    if p.is_file():
        return hash_file(p) if mode == "content" else "stat:" + _stat_sig(p)
    if p.is_dir():
        h = hashlib.sha256()
        for rel, fp in _walk_files(p):
            sig = hash_file(fp) if mode == "content" else _stat_sig(fp)
            h.update(f"{rel}\x00{sig}\n".encode("utf-8"))
        prefix = "sha256-dir:" if mode == "content" else "stat-dir:"
        return prefix + h.hexdigest()
    return None


def short(h: str | None, n: int = 12) -> str:
    """A readable prefix of a hash, for messages and the CSV."""
    if not h:
        return "-"
    _, _, digest = h.rpartition(":")
    return digest[:n]
