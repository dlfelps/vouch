"""Change classes and heuristics (SPEC §9), on synthetic baselines."""

import json
import sys

import pytest

from vouch import changes as ch
from vouch.config import Config


def cur(raw, *, type_="float", rendered=None, plain=None, source="run:r", kind="value"):
    r = rendered if rendered is not None else [str(raw)]
    return ch.Current("k", kind, type_, raw, r, plain if plain is not None else r, source)


def base(raw, *, type_="float", rendered=None, source="run:r", kind="value"):
    return {"kind": kind, "type": type_, "raw": raw, "rendered": rendered or [str(raw)],
            "plain": rendered or [str(raw)], "source": source, "acked": "2026-09-10T00:00:00Z",
            "by": "me", "why": "x"}


def classify(tmp_path, old, new, threshold=0.10):
    cfg = Config(tmp_path, {"changes": {"rel_threshold": threshold}})
    baseline = {"k": old} if old is not None else {}
    out = ch.compute(cfg, baseline, {"k": new}, {"k": [ch.Citing("p.tex", 3, "s")]})
    return out[0] if out else None


def test_unchanged_and_new(tmp_path):
    assert classify(tmp_path, base(0.5), cur(0.5)) is None
    assert classify(tmp_path, base(0.5), cur(0.5 + 1e-15, rendered=["0.5"])) is None   # float noise
    assert classify(tmp_path, None, cur(0.5)).cls == "new"


def test_reformatted_and_hidden(tmp_path):
    assert classify(tmp_path, base(0.5, rendered=["50%"]),
                    cur(0.5, rendered=["50.0%"])).cls == "reformatted"
    hidden = classify(tmp_path, base(0.9321, rendered=["93.2%"]), cur(0.9324, rendered=["93.2%"]))
    assert hidden.cls == "hidden" and not hidden.pending


def test_changed_vs_suspicious(tmp_path):
    small = classify(tmp_path, base(0.932, rendered=["93.2"]), cur(0.935, rendered=["93.5"]))
    assert small.cls == "changed" and small.pending and small.reasons == []
    assert small.citations[0].sentence == "s"

    def reasons(old, new, **kw):
        c = classify(tmp_path, base(old, **kw), cur(new, **kw))
        assert c.cls == "suspicious", c
        return " ".join(c.reasons)

    assert "large move" in reasons(0.9, 0.7)
    assert "sign flip" in reasons(0.3, -0.2)
    assert "moved to or from zero" in reasons(0.0, 0.2)
    assert "order-of-magnitude" in reasons(0.01, 0.2)
    assert "not finite" in reasons(0.5, {"$float": "nan"})


def test_stat_sample_size_and_type_and_source(tmp_path):
    old = base({"mean": 0.9, "std": 0.01, "n": 5}, type_="stat", rendered=["90"])
    new = cur({"mean": 0.9, "std": 0.01, "n": 3}, type_="stat", rendered=["90"])
    c = classify(tmp_path, old, new)
    assert c.cls == "suspicious" and "sample size changed (n=5 -> 3)" in c.reasons

    c = classify(tmp_path, base(0.9), cur({"mean": 0.9, "std": 0.0, "n": 1}, type_="stat"))
    assert c.cls == "suspicious" and c.reasons[0].startswith("type changed")

    c = classify(tmp_path, base(0.9, source="run:a"), cur(0.9, source="run:b"))
    assert c.cls == "suspicious" and "now produced by b (was a)" in c.reasons


def test_threshold_is_configurable(tmp_path):
    assert classify(tmp_path, base(0.9, rendered=["a"]), cur(0.85, rendered=["b"]),
                    threshold=0.5).cls == "changed"


def test_claims_and_figures(tmp_path):
    old = base({"holds": True, "values": {"a": 1}}, type_="claim", kind="claim", rendered=["HOLDS"])
    new = cur({"holds": True, "values": {"a": 2}}, type_="claim", kind="claim", rendered=["HOLDS"])
    c = classify(tmp_path, old, new)
    assert c.cls == "changed" and c.reasons == ["the values behind the claim moved"]
    f = classify(tmp_path, base("sha256:a", type_="figure", kind="figure"),
                 cur("sha256:b", type_="figure", kind="figure", rendered=[]))
    assert f.cls == "figure-changed"


def test_delta_in_points_for_percentages(tmp_path):
    c = classify(tmp_path, base(0.802, rendered=[r"80.2\%"]), cur(0.771, rendered=[r"77.1\%"]))
    assert ch.describe_delta(c) == "Δ -3.1 pts, -3.9% relative"
    assert ch.was_text(c) == "CHANGED: was 80.2% (acked 2026-09-10)"


def test_auto_ack_and_ack_write_baseline_and_history(tmp_path):
    cfg = Config(tmp_path)
    baseline = {}
    changes = ch.compute(cfg, baseline, {"k": cur(0.5)}, {})
    done = ch.auto_acknowledge(cfg, baseline, changes)
    assert [c.cls for c in done] == ["new"]
    assert ch.load_baseline(cfg)["k"]["raw"] == 0.5
    changes = ch.compute(cfg, ch.load_baseline(cfg), {"k": cur(0.7, rendered=["0.7"])}, {})
    ch.acknowledge(cfg, baseline, changes, "checked the prose")
    assert ch.load_baseline(cfg)["k"]["why"] == "checked the prose"
    events = [json.loads(ln) for ln in (cfg.store / "history.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "ack" and events[-1]["new"] == 0.7


def test_on_change_runs_once_per_change(tmp_path):
    out = tmp_path / "hook-out.json"
    script = tmp_path / "hook.py"
    script.write_text(f"import sys; open({str(out)!r}, 'a').write(sys.stdin.read() + '\\n')\n")
    cfg = Config(tmp_path, {"changes": {"on_change": [sys.executable, str(script)]}})
    changes = [ch.Change("k", "changed", [], base(0.5), cur(0.6), [])]
    fresh, err = ch.notify(cfg, changes)
    assert err is None and len(fresh) == 1
    payload = json.loads(out.read_text().splitlines()[0])
    assert payload["changes"][0]["key"] == "k" and payload["changes"][0]["class"] == "changed"
    fresh, _ = ch.notify(cfg, changes)          # same change again: not re-notified
    assert fresh == [] and len(out.read_text().splitlines()) == 1


def test_baseline_file_is_one_key_per_line(tmp_path):
    cfg = Config(tmp_path)
    ch.save_baseline(cfg, {"b": {"raw": 1}, "a": {"raw": 2}})
    lines = (cfg.store / "acknowledged.json").read_text().splitlines()
    assert lines[3].startswith('    "a": ') and lines[4].startswith('    "b": ')
    assert ch.load_baseline(cfg) == {"a": {"raw": 2}, "b": {"raw": 1}}
