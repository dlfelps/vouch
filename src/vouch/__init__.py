"""vouch: every number in your paper, vouched for by the code that produced it.

Record values where they are computed::

    import vouch
    vouch.record("cifar.resnet.acc", acc, fmt=".1pct", desc="top-1 test accuracy")
    vouch.record_all(metrics, prefix="cifar.resnet")

    with vouch.run("cifar_resnet", params=args) as run:
        run.input("data/cifar10.npz")
        run.record_all(evaluate(model), prefix="cifar.resnet")

then cite them in LaTeX as ``\\vouch{cifar.resnet.acc}``. See SPEC.md.
"""

from .api import (Run, active_run, artifact, claim, input, params, record, record_all, run,
                  table)
from .values import Stat

__version__ = "0.1.0.dev0"

# Track which functions the experiment executes, from the moment vouch is imported
# (SPEC 8.2). The CLI stops this at once; it has nothing to track.
from .tracing import tracker as _tracker  # noqa: E402

_tracker.start()

__all__ = ["Run", "Stat", "active_run", "artifact", "claim", "input", "params", "record",
           "record_all", "run", "table", "__version__"]
