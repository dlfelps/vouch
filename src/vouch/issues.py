"""The issue model shared by build, check and the JSON output (SPEC §11, §13.6)."""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping

SEVERITIES = ("error", "warning", "info")

# the order to fix things in: each group tends to unlock the ones after it
PRIORITY = ("config", "store-edited", "bad-record", "derive-error", "derive-cycle",
            "unknown-key", "key-conflict", "alias-target", "untracked-input", "out-of-sync",
            "format", "incomplete", "tampered", "stale", "upstream-stale", "false-claim",
            "figure-stale", "figure-tampered", "suspicious", "changed", "figure-changed",
            "fragile", "pending", "non-finite", "figure-missing", "figure-untracked",
            "no-source", "bare-number", "imported", "no-description", "env-drift", "absent",
            "table")


@dataclasses.dataclass
class Issue:
    check: str
    severity: str                      # error | warning | info
    message: str
    file: str | None = None
    line: int | None = None
    fix: str | None = None
    fix_kind: str = "command"          # command | edit | build | human
    subject: str | None = None
    detail: dict = dataclasses.field(default_factory=dict)
    where_all: list[tuple[str, int]] = dataclasses.field(default_factory=list)

    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.file and self.line else (self.file or "")

    def to_json(self) -> dict[str, Any]:
        where = [{"file": f, "line": ln} for f, ln in (self.where_all or
                                                        ([(self.file, self.line)] if self.file else []))]
        out = {"check": self.check, "severity": self.severity, "message": self.message,
               "subject": self.subject, "where": where}
        if self.fix:
            out["fix"] = {"kind": self.fix_kind, "value": self.fix}
        if self.detail:
            out["detail"] = self.detail
        return out


def apply_severity(issues: list[Issue], overrides: Mapping[str, str] | None,
                   strict: bool) -> list[Issue]:
    """Config overrides first, then --strict turns every warning into an error."""
    out = []
    for i in issues:
        sev = (overrides or {}).get(i.check, i.severity)
        if sev not in SEVERITIES:
            sev = i.severity
        if strict and sev == "warning":
            sev = "error"
        out.append(dataclasses.replace(i, severity=sev))
    return out


def ordered(issues: list[Issue]) -> list[Issue]:
    rank = {c: n for n, c in enumerate(PRIORITY)}
    sev = {"error": 0, "warning": 1, "info": 2}
    return sorted(issues, key=lambda i: (sev.get(i.severity, 3), rank.get(i.check, len(rank)),
                                         i.file or "", i.line or 0, i.message))
