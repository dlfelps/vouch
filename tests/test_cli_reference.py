"""SPEC §12 and the CLI docs describe the parser that exists."""

import re
from pathlib import Path

from vouch.cli import make_parser

ROOT = Path(__file__).resolve().parents[1]


def commands() -> dict:
    parser = make_parser()
    return dict(parser._subparsers._group_actions[0].choices)


def options(sub) -> set[str]:
    return {s for a in sub._actions for s in a.option_strings}


def section_12() -> str:
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    return spec[spec.index("## 12. CLI reference"):spec.index("### 12.1")]


def test_every_flag_in_the_cli_table_exists():
    subs = commands()
    for row in section_12().splitlines():
        if not row.startswith("| `vouch "):
            continue
        cell = row.split(" | ")[0]
        names = re.findall(r"`vouch ([a-z]+)", cell)
        assert names and all(n in subs for n in names), row
        have = set().union(*(options(subs[n]) for n in names))
        for flag in re.findall(r"--[a-z][a-z-]*", cell):
            assert flag in have, f"{flag} of {names} is in SPEC §12 but not in the parser"


def json_commands_named(text: str) -> set[str] | None:
    """The commands a 'take --json' sentence names, or None if the text has none."""
    m = re.search(r"These commands also accept `--json`[^:]*:([^.]*)\.", text)
    return None if m is None else set(re.findall(r"`([a-z]+)`", m.group(1)))


def test_the_json_claim_names_exactly_the_commands_that_take_json():
    # SPEC §12 and docs/cli/index.md said "Every command accepts --json"; init, watch,
    # run, import, ack, accept, sync, hook, review, catalog and mcp never did.
    subs = commands()
    with_json = {n for n, sp in subs.items() if "--json" in options(sp)}
    for text in (section_12(), (ROOT / "docs" / "cli" / "index.md").read_text(encoding="utf-8")):
        assert "Every command accepts `--json`" not in text or with_json == set(subs)
        named = json_commands_named(text)
        assert named == with_json, named ^ with_json if named else "no --json sentence"
