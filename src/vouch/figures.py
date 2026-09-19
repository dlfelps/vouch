"""Figures saved with matplotlib become artifacts of the run (SPEC §4.7).

``Figure.savefig`` -- and so ``plt.savefig`` -- is wrapped: every figure saved to a
path while vouch is recording becomes a figure artifact of the active run, with the
file:line of the ``savefig`` call. Its content hash is taken when the run ends, so
``\\includegraphics`` of that file is checked for freshness like any value.

matplotlib is never imported by vouch. If it is already loaded, it is patched now;
otherwise a finder on ``sys.meta_path`` waits for ``matplotlib.figure`` to be
imported, patches it, and steps aside. Saving to a file object is not tracked.
"""

from __future__ import annotations

import functools
import importlib.abc
import importlib.util
import os
import sys

TARGET = "matplotlib.figure"
_PKG_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
_enabled = True


def disable() -> None:
    """Stop recording saved figures (the CLI records nothing)."""
    global _enabled
    _enabled = False


def _caller_site() -> str:
    """file:line of the first frame outside vouch and matplotlib."""
    f = sys._getframe(2)
    while f is not None:
        fn = os.path.normcase(os.path.abspath(f.f_code.co_filename))
        parts = fn.replace("\\", "/").split("/")
        if not fn.startswith(_PKG_DIR) and "matplotlib" not in parts:
            break
        f = f.f_back
    if f is None:
        return "?"
    from .api import _project
    return f"{_project().config.rel(f.f_code.co_filename)}:{f.f_lineno}"


def _saved_path(fname, fmt: str | None) -> str | None:
    if not isinstance(fname, (str, os.PathLike)):
        return None                                   # a file object: nothing to point at
    path = os.fspath(fname)
    if not os.path.splitext(path)[1]:                 # matplotlib adds the extension
        if fmt is None:
            try:
                import matplotlib
                fmt = matplotlib.rcParams["savefig.format"]
            except Exception:
                fmt = "png"
        path = f"{path}.{fmt}"
    return path


def patch(module) -> None:
    fig = getattr(module, "Figure", None)
    orig = getattr(fig, "savefig", None)
    if orig is None or getattr(orig, "__vouch__", False):
        return

    @functools.wraps(orig)
    def savefig(self, fname, *args, **kwargs):
        result = orig(self, fname, *args, **kwargs)
        if _enabled:
            try:
                path = _saved_path(fname, kwargs.get("format"))
                if path is not None:
                    from .api import _figure_saved
                    _figure_saved(path, _caller_site())
            except Exception as exc:          # bookkeeping never breaks the experiment
                from .api import _warn
                _warn(f"could not record the saved figure {fname!r}: {exc}")
        return result

    savefig.__vouch__ = True
    fig.savefig = savefig


class _Loader(importlib.abc.Loader):
    """Runs the real loader, then patches the module."""

    def __init__(self, inner):
        self.inner = inner

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        module.__loader__ = self.inner
        if module.__spec__ is not None:
            module.__spec__.loader = self.inner
        self.inner.exec_module(module)
        patch(module)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != TARGET:
            return None
        _remove()
        spec = importlib.util.find_spec(fullname)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _Loader(spec.loader)
        return spec


_finder: _Finder | None = None


def _remove() -> None:
    global _finder
    if _finder is not None and _finder in sys.meta_path:
        sys.meta_path.remove(_finder)
    _finder = None


def install() -> None:
    global _finder
    mod = sys.modules.get(TARGET)
    if mod is not None:
        patch(mod)
        return
    if _finder is None:
        _finder = _Finder()
        sys.meta_path.insert(0, _finder)
