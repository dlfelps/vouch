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


def hash_path(path: str | os.PathLike, mode: str = "content", file_hash=hash_file) -> str | None:
    """Hash of a file or directory, or None if it doesn't exist."""
    p = Path(path)
    if mode not in ("content", "stat"):
        raise ValueError(f"unknown hashing mode {mode!r}")
    if p.is_file():
        return file_hash(p) if mode == "content" else "stat:" + _stat_sig(p)
    if p.is_dir():
        h = hashlib.sha256()
        for rel, fp in _walk_files(p):
            sig = file_hash(fp) if mode == "content" else _stat_sig(fp)
            h.update(f"{rel}\x00{sig}\n".encode("utf-8"))
        prefix = "sha256-dir:" if mode == "content" else "stat-dir:"
        return prefix + h.hexdigest()
    return None


def mode_of(recorded: str | None) -> str:
    """Which mode produced a recorded hash, so it is re-checked the same way."""
    return "stat" if recorded and recorded.startswith("stat") else "content"


class HashCache:
    """Content hashes keyed by (size, mtime_ns), persisted in ``.vouch/cache/hashes.json``.

    ``vouch check`` must stay under a second; re-hashing a multi-gigabyte dataset
    on every check would not. A file is re-hashed only when its size or
    modification time changes.
    """

    def __init__(self, path: Path | None = None):
        import json
        self.path = path
        self.data: dict[str, list] = {}
        self.dirty = False
        if path is not None and path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def file(self, p: Path) -> str:
        st = p.stat()
        key = str(p.resolve())
        hit = self.data.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        h = hash_file(p)
        self.data[key] = [st.st_size, st.st_mtime_ns, h]
        self.dirty = True
        return h

    def path_hash(self, p: str | os.PathLike, mode: str = "content") -> str | None:
        return hash_path(p, mode, file_hash=self.file)

    def save(self) -> None:
        import json
        if self.path is None or not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")
            self.dirty = False
        except OSError:
            pass


def short(h: str | None, n: int = 12) -> str:
    """A readable prefix of a hash, for messages and the CSV."""
    if not h:
        return "-"
    _, _, digest = h.rpartition(":")
    return digest[:n]
