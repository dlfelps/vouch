"""``python -m vouch.exec script.py [args...]``: run a script under vouch, unchanged.

Tracking starts before the script's first line, so every function it runs is
tracked by function (nothing is "imported before vouch"), and functions listed in
``[[track]]`` in vouch.toml have their results recorded -- without the script
importing vouch at all. The recorded command is the one to re-run:
``python -m vouch.exec train.py --model vit``.
"""

from __future__ import annotations

import os
import runpy
import sys

USAGE = "usage: python -m vouch.exec script.py [args...]"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr)
        return 2
    script = argv[0]
    if not os.path.isfile(script):
        print(f"vouch.exec: no such script: {script}\n{USAGE}", file=sys.stderr)
        return 2
    from .tracing import tracker
    here = os.path.dirname(os.path.abspath(script))
    tracker.configure(here)                  # the script's project, not the cwd's
    sys.argv = argv
    sys.path.insert(0, here)
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
