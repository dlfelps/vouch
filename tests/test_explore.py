"""vouch explore: the registry grouped by script and function, served read-only."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from vouch import cli
from vouch.build import build
from vouch.config import Config
from vouch.explore import _State, make_handler, page, registry, stamp


@pytest.fixture
def proj(project):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n'
                                '[metrics]\n"*.acc" = { fmt = ".1pct", desc = "accuracy" }\n')
    project.write("experiments/train.py", '''
        import vouch


        @vouch.track(over="seed")
        def evaluate(dataset, model, seed=0):
            return {"acc": 0.9 + seed / 100}


        def main():
            vouch.params({"epochs": 3})
            for s in range(3):
                evaluate("cifar", "resnet", seed=s)
            vouch.record("cifar.n_test", 100, desc="test images")
            vouch.table("main", [{"model": "resnet", "acc": 0.91}], row_key="model", desc="t")


        main()
        vouch.claim("done", True, desc="it ran")
    ''')
    project.write("vouch_values.py", '''
        import vouch

        vouch.alias("r", "evaluate.cifar.resnet")

        @vouch.derive("cifar.double", desc="twice the accuracy")
        def double(v):
            return 2 * v["r.acc.mean"]
    ''')
    project.write("paper/main.tex", "\\documentclass{article}\\usepackage{vouch}\\begin{document}\n"
                  "\\vouch{r.acc} \\vouch{cifar.double}\n\\end{document}\n")
    project.run("experiments/train.py", check=True)
    build(Config.load(project.root))
    return project


def groups(data):
    return {s["script"]: {f["name"]: f for f in s["functions"]} for s in data["scripts"]}


def test_values_are_grouped_by_script_then_function(proj):
    data = registry(Config.load(proj.root))
    g = groups(data)
    assert list(g) == ["experiments/train.py", "vouch_values.py"]
    train = g["experiments/train.py"]
    assert list(train) == ["evaluate", "main", "top level", "parameters"]
    ev = train["evaluate"]
    assert ev["how"] == "@vouch.track" and ev["where"] == "experiments/train.py:4"
    acc = ev["items"][0]
    assert acc["key"] == "evaluate.cifar.resnet.acc" and acc["value"] == "91.0 ± 1.0%"
    assert acc["snippet"] == "\\vouch{evaluate.cifar.resnet.acc}"
    assert acc["call"] == "evaluate(dataset=cifar, model=resnet) over seed=0..2 (3 calls)"
    assert acc["each"].startswith("seed=0: 0.9 (") and "; seed=1: 0.91 (" in acc["each"]
    assert acc["time"].startswith("took ") and "per call" in acc["time"]
    assert [p["name"] for p in acc["parts"]][:3] == ["mean", "std", "n"]
    assert acc["state"] == "fresh" and acc["cited"] == []
    # a record() inside main() sits under main; a claim at top level under "top level"
    main = train["main"]
    assert main["how"] == "recorded" and main["where"] == "experiments/train.py:9"
    table = next(i for i in main["items"] if i["key"] == "main")
    assert table["snippet"] == "\\vouchtable{main}"
    assert table["tabular"].startswith("\\begin{tabular}{lr}\n  \\toprule\n  model & acc \\\\")
    assert table["rows"][0][1] == {"key": "main.resnet.acc", "value": "91.0%", "cited": []}
    claim = train["top level"]["items"][0]
    assert claim["snippet"] == "\\vouchclaim{done}{it ran}" and claim["value"] == "holds"
    assert train["parameters"]["items"][0]["key"] == "experiments.train.param.epochs"
    # derived values and aliases
    vv = g["vouch_values.py"]
    double = vv["double"]["items"][0]
    assert vv["double"]["how"] == "derived" and double["cited"] == ["paper/main.tex:2"]
    assert double["derived"]["deps"] == ["r.acc.mean"] and double["derived"]["runs"] == ["experiments.train"]
    alias = next(i for i in vv["aliases"]["items"] if i["key"] == "r.acc")
    assert alias["alias_of"] == "evaluate.cifar.resnet.acc" and alias["cited"] == ["paper/main.tex:2"]
    assert data["counts"] == {"values": 4, "claims": 1, "tables": 1, "figures": 0, "runs": 1}
    assert data["issues"] == []


def test_explore_never_runs_project_code(proj):
    edit_to = "raise SystemExit('explore must not import this')\n"
    path = proj.root / "vouch_values.py"
    path.write_text(path.read_text(encoding="utf-8") + edit_to, encoding="utf-8")
    data = registry(Config.load(proj.root))          # derived values from derived.json
    assert "vouch_values.py" in groups(data)
    assert any("out of date" in i["message"] for i in data["issues"])


def test_snapshot_embeds_the_data(proj, tmp_path, capsys):
    out = tmp_path / "snap.html"
    assert cli.main(["explore", "--root", str(proj.root), "--html", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "/*__VOUCH_DATA__*/null" not in html and "evaluate.cifar.resnet.acc" in html
    assert "</script>" not in html.split("const EMBEDDED = ", 1)[1].split(";\n", 1)[0]
    assert "wrote" in capsys.readouterr().out
    assert "/*__VOUCH_DATA__*/null" in page()                  # the served page fetches instead


def test_cli_json(proj, capsys):
    assert cli.main(["explore", "--root", str(proj.root), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "explore" and out["registry"]["scripts"]


@pytest.fixture
def server(proj):
    from http.server import ThreadingHTTPServer
    state = _State(Config.load(proj.root))
    port = [0]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, port))
    port[0] = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield proj, port[0]
    httpd.shutdown()
    httpd.server_close()


def get(port, path, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if host:
        req.add_header("Host", host)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_server_serves_the_page_and_its_data(server):
    proj, port = server
    status, ctype, body = get(port, "/")
    assert status == 200 and ctype.startswith("text/html") and b"vouch explore" in body
    _, ctype, body = get(port, "/api/registry")
    data = json.loads(body)
    assert ctype == "application/json" and data["stamp"] == stamp(Config.load(proj.root))
    first = json.loads(get(port, "/api/stamp")[2])["stamp"]
    proj.run("experiments/train.py", check=True)              # a re-run changes what is shown
    assert json.loads(get(port, "/api/stamp")[2])["stamp"] != first
    assert json.loads(get(port, "/api/registry")[2])["stamp"] != first


def test_server_refuses_other_hosts_and_paths(server):
    _, port = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/registry", host="evil.example")
    assert exc.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/vouch.toml")
    assert exc.value.code == 404
