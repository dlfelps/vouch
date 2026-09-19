"""vouch: every number in your paper, vouched for by the code that produced it.

Record what an experiment function returns, keyed by its arguments::

    import vouch

    @vouch.track(over="seed")
    def evaluate(dataset, model, seed=0):
        ...
        return {"acc": acc, "loss": loss}

or record values directly::

    vouch.record("cifar.resnet.acc", acc, desc="top-1 test accuracy")
    vouch.record_all(metrics, prefix="cifar.resnet")

Compute numbers *from* results in ``vouch_values.py``; ``vouch build`` evaluates it::

    @vouch.derive("cifar.gap", fmt=".1f", desc="ResNet minus ViT, points")
    def gap(v):
        return 100 * (v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"])

then cite them in LaTeX as ``\\vouch{cifar.gap}``. See SPEC.md.

The public functions load on first use, so ``import vouch`` stays cheap: the
command line (and the Claude Code hook, run after every edit) pays only for what
it uses.
"""

import os as _os
import sys as _sys

__version__ = "0.1.0.dev0"

_PUBLIC = {
    "Run": "api", "active_run": "api", "artifact": "api", "claim": "api", "input": "api",
    "params": "api", "record": "api", "record_all": "api", "run": "api", "table": "api",
    "alias": "derived", "derive": "derived", "expect": "derived", "track": "tracked",
    "Stat": "values", "Verdict": "verdict", "all_of": "verdict", "any_of": "verdict",
    "approx": "verdict", "between": "verdict", "ge": "verdict", "gt": "verdict",
    "le": "verdict", "lt": "verdict",
}
__all__ = sorted(_PUBLIC) + ["__version__"]


def __getattr__(name: str):
    mod = _PUBLIC.get(name)
    if mod is None:
        raise AttributeError(f"module 'vouch' has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(f"{__name__}.{mod}"), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_PUBLIC))


def _is_cli() -> bool:
    """True when this process is the ``vouch`` command itself, which runs no experiment."""
    prog = _os.path.basename(_sys.argv[0] if _sys.argv else "").lower()
    if prog in ("vouch", "vouch.exe", "vouch-script.py"):
        return True
    argv = list(getattr(_sys, "orig_argv", []))
    return "-m" in argv and argv.index("-m") + 1 < len(argv) and \
        argv[argv.index("-m") + 1] in ("vouch", "vouch.cli")


if not _is_cli():
    # Track which functions the experiment executes, from the moment vouch is
    # imported (SPEC 8.2), and record figures saved with matplotlib (SPEC 4.7).
    from .tracing import tracker as _tracker

    _tracker.start()

    from . import figures as _figures

    _figures.install()
