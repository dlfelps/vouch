"""``vouch watch``: rebuild whenever a value, a values module or the paper changes.

Polling, with the standard library only: a signature ``(mtime_ns, size)`` per watched
file, compared every ``interval`` seconds. It behaves the same on every OS and on
network filesystems, and needs nothing beyond the base install.

Watched: ``.vouch/runs/*.json`` (new values), ``vouch.toml``, the values modules, and
every ``.tex`` file of every paper (re-read after each build, so a newly ``\\input``
file is picked up). Never watched: what the build writes, so a build can't trigger
itself. The watcher never runs an experiment; it only re-renders what was recorded.
"""

from __future__ import annotations

import dataclasses
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import Config, ConfigError
from .index import Index
from .store import RecordError, runs_dir

Sig = tuple[int, int]


@dataclasses.dataclass
class Rebuild:
    result: object = None              # build.BuildResult, when the build succeeded
    moved: list = dataclasses.field(default_factory=list)   # vdiff.ValueDiff since the last build
    error: str | None = None
    then_rc: int | None = None         # exit code of --then, if it ran


class Watcher:
    def __init__(self, root: Path, *, notify: bool = True, then: str | None = None):
        self.root = Path(root).resolve()
        self.notify = notify
        self.then = then
        self.cfg = Config.load(self.root)
        self.last: Index | None = None
        self.tex: set[Path] = set()
        self.generated: set[Path] = set()
        self.sigs: dict[Path, Sig] = self.snapshot()

    # -- what to watch -----------------------------------------------------------

    def targets(self) -> set[Path]:
        from .valuesmod import values_modules
        out = set(runs_dir(self.cfg.store).glob("*.json"))
        out.add(self.root / "vouch.toml")
        out.update(values_modules(self.cfg))
        out.update(self.tex)
        return {p.resolve() for p in out} - self.generated

    def snapshot(self) -> dict[Path, Sig]:
        sigs = {}
        for p in self.targets():
            try:
                st = p.stat()
            except OSError:
                continue
            sigs[p] = (st.st_mtime_ns, st.st_size)
        return sigs

    def scan(self) -> list[Path]:
        """The watched files that appeared, changed or went away since the last scan."""
        new = self.snapshot()
        changed = sorted(p for p in set(new) | set(self.sigs) if new.get(p) != self.sigs.get(p))
        self.sigs = new
        return changed

    # -- building ----------------------------------------------------------------

    def rebuild(self) -> Rebuild:
        """``vouch build``, and what moved since the previous one."""
        from .build import BuildError, build
        from .vdiff import cite_counts, compare
        try:
            self.cfg = Config.load(self.root)
            res = build(self.cfg, notify=self.notify)
        except (BuildError, ConfigError, RecordError, FileNotFoundError) as exc:
            self.sigs = self.snapshot()
            return Rebuild(error=str(exc))
        except Exception as exc:           # noqa: BLE001 -- a watcher outlives a bad edit
            self.sigs = self.snapshot()
            return Rebuild(error=f"{type(exc).__name__}: {exc}")
        ctx = res.ctx
        moved = []
        if self.last is not None:
            moved, _ = compare(self.cfg, self.last, ctx.idx,
                               cited=cite_counts(ctx.plans, ctx.idx.tables))
        self.last = ctx.idx
        self.tex = {Path(f).resolve() for pl in ctx.plans for f in pl.doc.files}
        self.generated = {Path(p).resolve() for pl in ctx.plans for p in pl.files}
        self.generated |= {Path(p).resolve() for p in res.written}
        self.sigs = self.snapshot()        # what the build itself touched is not a change
        out = Rebuild(res, moved)
        if self.then and res.written:
            try:
                out.then_rc = subprocess.run(self.then, shell=True, cwd=self.root).returncode
            except OSError:
                out.then_rc = -1
            self.sigs = self.snapshot()
        return out

    # -- the loop ----------------------------------------------------------------

    def run(self, report: Callable[[list[Path], Rebuild], None], *, interval: float = 1.0,
            cycles: int | None = None) -> None:
        """Build now, then after every change. ``cycles`` bounds the loop (tests)."""
        report([], self.rebuild())
        n = 0
        while cycles is None or n < cycles:
            n += 1
            time.sleep(interval)
            changed = self.scan()
            if not changed:
                continue
            while True:                    # settle: one build for a burst of writes
                time.sleep(interval)
                more = self.scan()
                if not more:
                    break
                changed = sorted(set(changed) | set(more))
            report(changed, self.rebuild())
