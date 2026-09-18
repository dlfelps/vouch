from pathlib import Path

import pytest

from vouch import fmt
from vouch.config import Config, ConfigError, discover_root, expand_desc


@pytest.mark.parametrize("spec,canonical", [
    (".1f", ".1f"), (".1pct", ".1pct"), (".1%", ".1pct"), ("{:.1%}", ".1pct"), (":.2f", ".2f"),
    (",d", ",d"), (".2e", ".2e"), (".2fx", ".2fx"), ("+.1pct", "+.1pct"), ("dto", "dto"),
    (".1pctci", ".1pctci"), (".1fu", ".1fu"), ("s", "s"), ("x", "x"), (".0f", ".0f"),
])
def test_normalize(spec, canonical):
    assert fmt.normalize(spec) == canonical


@pytest.mark.parametrize("bad", [".1q", "%%", ".f.", "pct1", ".2fxx", "1.2f"])
def test_normalize_rejects(bad):
    with pytest.raises(ValueError):
        fmt.normalize(bad)


def test_named_formats_are_kept_by_name():
    assert fmt.normalize("pct1", {"pct1": ".1pct"}) == "pct1"
    assert fmt.normalize(None) is None and fmt.normalize("  ") is None


def test_canonical_is_latex_safe():
    for spec in (".1%", "{:+,.2f}", ".2e"):
        out = fmt.normalize(spec)
        assert all(c.isalnum() or c in ".,+-" for c in out), out


def test_metric_defaults_longest_pattern_wins_per_field(tmp_path: Path):
    cfg = Config(tmp_path, {"metrics": {
        "*.acc": {"fmt": ".1pct", "better": "higher", "desc": "top-1 accuracy, {1} on {0}"},
        "cifar.*.acc": {"desc": "CIFAR-10 accuracy of {1}"},
    }})
    d = cfg.metric_defaults("cifar.resnet.acc")
    assert d == {"fmt": ".1pct", "better": "higher", "desc": "CIFAR-10 accuracy of resnet"}
    assert cfg.metric_defaults("imagenet.vit.acc")["desc"] == "top-1 accuracy, vit on imagenet"
    assert cfg.metric_defaults("imagenet.vit.loss") == {}


def test_metrics_reject_unknown_fields(tmp_path: Path):
    with pytest.raises(ConfigError):
        Config(tmp_path, {"metrics": {"*.acc": {"format": ".1pct"}}})


def test_expand_desc():
    assert expand_desc("{-1} F1, {1} on {0}", "cifar.resnet.f1.macro") == "macro F1, resnet on cifar"
    assert expand_desc("{key} / {9}", "a.b") == "a.b / "


def test_load_and_defaults(tmp_path: Path):
    (tmp_path / "vouch.toml").write_text('[freshness]\ngranularity = "module"\n', encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.get("freshness", "granularity") == "module"
    assert cfg.get("freshness", "input_hashing") == "content"      # default kept
    assert cfg.get("changes", "rel_threshold") == 0.10


def test_bad_toml(tmp_path: Path):
    (tmp_path / "vouch.toml").write_text("[freshness\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(tmp_path)


def test_discover_root(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VOUCH_ROOT", raising=False)
    (tmp_path / "vouch.toml").write_text("", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert discover_root(sub) == (tmp_path.resolve(), "config")
    monkeypatch.setenv("VOUCH_ROOT", str(sub))
    assert discover_root(tmp_path) == (sub.resolve(), "env")


def test_first_party(tmp_path: Path):
    cfg = Config(tmp_path)
    (tmp_path / "src").mkdir()
    for p in ("src/models.py", ".venv/lib/x.py", "lib/site-packages/y.py", "notes.txt"):
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
    assert cfg.is_first_party(tmp_path / "src/models.py")
    assert not cfg.is_first_party(tmp_path / ".venv/lib/x.py")
    assert not cfg.is_first_party(tmp_path / "lib/site-packages/y.py")
    assert not cfg.is_first_party(tmp_path / "notes.txt")
    assert not cfg.is_first_party(tmp_path.parent / "elsewhere.py")
    assert cfg.rel(tmp_path / "src/models.py") == "src/models.py"
