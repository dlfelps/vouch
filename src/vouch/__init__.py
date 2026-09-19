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
"""

from .api import (Run, active_run, artifact, claim, input, params, record, record_all, run,
                  table)
from .derive import alias, derive
from .track import track
from .values import Stat
from .verdict import Verdict, all_of, any_of, approx, between, ge, gt, le, lt

__version__ = "0.1.0.dev0"

# Track which functions the experiment executes, from the moment vouch is imported
# (SPEC 8.2). The CLI stops this at once; it has nothing to track.
from .tracing import tracker as _tracker  # noqa: E402

_tracker.start()

# Figures saved with matplotlib inside a run become its artifacts (SPEC 4.7).
from . import figures as _figures  # noqa: E402

_figures.install()

__all__ = ["Run", "Stat", "Verdict", "active_run", "alias", "all_of", "any_of", "approx",
           "artifact", "between", "claim", "derive", "ge", "gt", "input", "le", "lt", "params",
           "record", "record_all", "run", "table", "track", "__version__"]
