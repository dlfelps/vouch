"""Project root discovery and ``vouch.toml``.

Every setting has a working default (SPEC §3.3), so a project with an empty
``vouch.toml`` -- or none at all -- still records.
"""

from __future__ import annotations

import copy
import fnmatch
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_NAME = "vouch.toml"
STORE_DIR = ".vouch"

DEFAULTS: dict[str, Any] = {
    "paper": [],
    "python": {
        "values_modules": ["vouch_values.py"],
        "first_party": [],
        "exclude": [".venv", "venv", "env", ".tox", ".nox", "build", "dist",
                    "node_modules", ".git", STORE_DIR, "__pycache__"],
    },
    "freshness": {"granularity": "function", "env_drift": "warn", "input_hashing": "content"},
    "inputs": {"external": []},
    "metrics": {},
    "format": {"rounding": "half_up", "default_float": ".3g", "siunitx": False, "named": {}},
    "latex": {"tooltip": "full", "highlight": "changed", "annotate": False},
    "changes": {"rel_threshold": 0.10, "claim_margin": 0.01, "on_change": []},
    "lint": {"level": "warn", "allow_years": True,
             "skip_envs": ["verbatim", "lstlisting", "minted", "comment"], "allow": []},
    "check": {"severity": {}},
    "hook": {"strict": False},
}


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
# root discovery
# ---------------------------------------------------------------------------

def find_config_root(start: Path) -> Path | None:
    """Nearest ancestor of ``start`` (inclusive) holding a ``vouch.toml``."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / CONFIG_NAME).is_file():
            return d
    return None


def find_git_root(start: Path) -> Path | None:
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return None


def discover_root(start: Path | None = None) -> tuple[Path, str]:
    """(root, how) -- how is "env", "config", "git" or "cwd".

    ``VOUCH_ROOT`` wins, then the nearest ``vouch.toml``. Without one the git top
    level is used, then ``start`` itself: adopting vouch must never break a run, so
    a missing config is a warning (issued by the caller), not an error.
    """
    env = os.environ.get("VOUCH_ROOT")
    if env:
        return Path(env).resolve(), "env"
    start = (start or Path.cwd()).resolve()
    found = find_config_root(start)
    if found is not None:
        return found, "config"
    git = find_git_root(start)
    if git is not None:
        return git, "git"
    return start, "cwd"


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _merge(base: dict, over: Mapping) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict) and k != "metrics":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """Merged configuration for one project root."""

    def __init__(self, root: Path, data: Mapping[str, Any] | None = None, *, path: Path | None = None):
        self.root = Path(root).resolve()
        self.path = path
        self.data = _merge(DEFAULTS, data or {})
        self._metric_patterns = self._compile_metrics(self.data.get("metrics") or {})

    @classmethod
    def load(cls, root: Path) -> "Config":
        path = Path(root) / CONFIG_NAME
        if not path.is_file():
            return cls(root)
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
        return cls(root, data, path=path)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        sec = self.data.get(section)
        if isinstance(sec, Mapping):
            return sec.get(key, default)
        return default

    @property
    def store(self) -> Path:
        return self.root / STORE_DIR

    @property
    def named_formats(self) -> dict[str, str]:
        return dict(self.get("format", "named", {}) or {})

    # -- [metrics] ---------------------------------------------------------

    @staticmethod
    def _compile_metrics(metrics: Mapping[str, Any]) -> list[tuple[str, dict]]:
        pats = []
        for pattern, meta in metrics.items():
            if not isinstance(meta, Mapping):
                raise ConfigError(f"[metrics] {pattern!r} must be a table like "
                                  f'{{ fmt = ".1pct", desc = "..." }}')
            unknown = set(meta) - {"fmt", "desc", "unit", "better"}
            if unknown:
                raise ConfigError(f"[metrics] {pattern!r}: unknown field(s) {sorted(unknown)}")
            pats.append((pattern, dict(meta)))
        # longest pattern first: the most specific rule wins
        pats.sort(key=lambda p: (-len(p[0]), p[0]))
        return pats

    def metric_defaults(self, key: str) -> dict[str, Any]:
        """Defaults for ``key`` from ``[metrics]``, field by field, most specific first.

        A key matched by both ``"*.acc"`` and ``"cifar.*.acc"`` takes each field from
        the longest pattern that sets it.
        """
        out: dict[str, Any] = {}
        for pattern, meta in self._metric_patterns:
            if fnmatch.fnmatchcase(key, pattern):
                for field, value in meta.items():
                    out.setdefault(field, value)
        if "desc" in out:
            out["desc"] = expand_desc(out["desc"], key)
        return out

    # -- first-party code ----------------------------------------------------

    def first_party_roots(self) -> list[Path]:
        roots = [self.root]
        for extra in self.get("python", "first_party", []) or []:
            p = Path(extra)
            roots.append((p if p.is_absolute() else self.root / p).resolve())
        return roots

    def is_first_party(self, path: str | os.PathLike) -> bool:
        """True for a .py file under a first-party root, outside excluded dirs and site-packages."""
        try:
            p = Path(path).resolve()
        except (OSError, RuntimeError):
            return False
        if p.suffix != ".py":
            return False
        parts_lower = {part.lower() for part in p.parts}
        if "site-packages" in parts_lower or "dist-packages" in parts_lower:
            return False
        exclude = set(self.get("python", "exclude", []) or [])
        for root in self.first_party_roots():
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if any(part in exclude for part in rel.parts[:-1]):
                return False
            return True
        return False

    def rel(self, path: str | os.PathLike) -> str:
        """Project-relative POSIX path, or the absolute POSIX path if outside the root."""
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / p
        p = p.resolve()
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()


_DESC_FIELD = re.compile(r"\{(-?\d+|key)\}")


def expand_desc(template: str, key: str) -> str:
    """Fill ``{0}``, ``{1}``, ``{-1}`` (key segments) and ``{key}`` in a desc template."""
    segments = key.split(".")

    def sub(m: re.Match) -> str:
        tok = m.group(1)
        if tok == "key":
            return key
        i = int(tok)
        try:
            return segments[i]
        except IndexError:
            return ""
    return _DESC_FIELD.sub(sub, str(template))
