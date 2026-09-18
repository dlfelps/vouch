import json
import random

from vouch import store
from vouch.hashing import hash_path


def sample_record() -> dict:
    return {
        "schema": store.SCHEMA, "run": "cifar_resnet", "status": "complete",
        "entry": "train.py", "command": ["python", "train.py"],
        "params": {"lr": 0.1, "model": "resnet"},
        "started": "2026-09-18T00:00:00Z", "duration_s": 1.5,
        "git": {"commit": "abc", "dirty": False},
        "env": {"python": "3.13.5", "platform": "win-amd64", "packages": {"numpy": "2.1.0"}},
        "code": {"granularity": "module", "units": {"train.py::<module>": "0123456789abcdef"},
                 "files": {"train.py": "fedcba9876543210"}},
        "inputs": {"data/x.csv": "sha256:00"},
        "values": {
            "cifar.resnet.acc": {"type": "float", "value": 0.93214, "fmt": ".1pct",
                                 "desc": "top-1 test accuracy", "site": "train.py:9"},
            "cifar.resnet.loss": {"type": "float", "value": {"$float": "nan"}, "site": "train.py:10"},
        },
        "claims": {"cifar.resnet.ok": {"holds": True, "desc": "fine", "site": "train.py:11"}},
        "artifacts": {}, "tables": {},
    }


def shuffled(d):
    if isinstance(d, dict):
        items = list(d.items())
        random.shuffle(items)
        return {k: shuffled(v) for k, v in items}
    return d


def test_dump_is_deterministic_and_order_independent():
    rec = store.seal(sample_record())
    text = store.dumps_record(rec)
    for seed in range(5):
        random.seed(seed)
        assert store.dumps_record(shuffled(rec)) == text


def test_dump_is_one_entry_per_line_and_valid_json():
    rec = store.seal(sample_record())
    text = store.dumps_record(rec)
    assert json.loads(text) == rec
    lines = text.splitlines()
    assert any(ln.strip().startswith('"cifar.resnet.acc": {"type": "float", "value": 0.93214')
               for ln in lines)
    assert lines[1] == '  "schema": "vouch/1",' and lines[-2].startswith('  "record_hash"')


def test_record_hash_detects_edits():
    rec = store.seal(sample_record())
    assert store.verify_record(rec)
    edited = json.loads(json.dumps(rec))
    edited["values"]["cifar.resnet.acc"]["value"] = 0.95
    assert not store.verify_record(edited)
    # key order and whitespace don't matter; content does
    assert store.verify_record(shuffled(rec))


def test_write_read_roundtrip_and_crlf(tmp_path):
    path = store.write_record(tmp_path / ".vouch", sample_record())
    assert (tmp_path / ".vouch" / ".gitignore").read_text() == "cache/\n"
    rec = store.read_record(path)
    assert store.verify_record(rec)
    # a CRLF checkout still verifies: the hash is over parsed content
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert store.verify_record(store.read_record(path))
    assert list(store.load_runs(tmp_path / ".vouch")) == ["cifar_resnet"]


def test_hash_path_file_dir_and_stat(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "a.csv").write_text("1,2\n")
    (d / "b.csv").write_text("3,4\n")
    h1 = hash_path(d)
    assert h1.startswith("sha256-dir:")
    (d / "b.csv").write_text("3,5\n")
    assert hash_path(d) != h1
    (d / "b.csv").rename(d / "c.csv")
    assert hash_path(d) != h1
    assert hash_path(d / "a.csv").startswith("sha256:")
    assert hash_path(d / "a.csv", "stat").startswith("stat:")
    assert hash_path(tmp_path / "missing") is None
