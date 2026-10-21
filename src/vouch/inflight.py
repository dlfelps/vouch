"""Runs in progress: a marker per run while it records (``.vouch/cache/running/<id>.json``).

A run is written to the store only when it ends, so a long one was invisible while it
lasted: `vouch status` called its old record stale and offered the re-run command, and a key
it was about to record was owed with no word that it was coming. While a run records, its
marker holds the run id, the process id, the start and the entry script; `status` shows it
as running, or as interrupted when that process is gone (killed before the run could end,
which leaves the marker behind until the run is next recorded). The markers live in the
store's cache, which is not committed. Writing one never fails a run.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

DIR = "cache/running"


def _path(store: Path, run_id: str) -> Path:
    return Path(store) / DIR / f"{run_id}.json"


def mark(store: Path, run_id: str, *, entry: str | None, command: str) -> None:
    """Name ``run_id`` as recording now, in this process."""
    started = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        p = _path(store, run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"run": run_id, "pid": os.getpid(), "started": started,
                                 "entry": entry, "command": command}, sort_keys=True),
                     encoding="utf-8")
    except OSError:
        pass


def unmark(store: Path, run_id: str) -> None:
    """The run has ended, recorded or discarded."""
    try:
        _path(store, run_id).unlink()
    except OSError:
        pass


def running(store: Path) -> list[dict]:
    """Every marker, each with ``alive``: whether its process is still there."""
    d = Path(store) / DIR
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict):
            continue
        m["alive"] = alive(m.get("pid"))
        out.append(m)
    return out


def since(m: dict) -> str:
    """The start of a marker's run as `status` prints times: ``2026-10-21 16:48``."""
    return str(m.get("started", ""))[:16].replace("T", " ")


def alive(pid) -> bool:
    """Whether process ``pid`` exists (a recycled id reads as alive: rare, and harmless)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                       # it exists, under another user
    except OSError:
        return False
    return True


def _alive_windows(pid: int) -> bool:
    # not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT. Ask for the exit code instead
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    query_limited_information, still_active = 0x1000, 259
    h = k.OpenProcess(query_limited_information, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        k.CloseHandle(h)
