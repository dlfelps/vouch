"""The ``vouch`` command's entry point.

Claude Code runs ``vouch hook claude`` after *every* file edit, most of them not to
a ``.tex`` file. Those return here, having imported nothing but ``json``; only a
tex edit (and every other command) loads the real command line.
"""

import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["hook", "claude"]:
        data = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        if ".tex" not in data:                       # not a tex edit: nothing to check
            return 0
        from .edithook import run_edit_hook
        code, text = run_edit_hook(data)
        if text:
            sys.stderr.write(text)
        return code
    from .cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
