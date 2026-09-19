"""vouch mcp: the lookups as MCP tools (SPEC §13.7), stdlib JSON-RPC over stdio."""

import io
import json
import subprocess
import sys

import pytest

from vouch.build import build
from vouch.config import Config
from vouch.mcp_server import TOOLS, Server, serve

EXP = '''
    import vouch

    @vouch.track(over="seed", desc="top-1 test accuracy")
    def evaluate(dataset, model, seed=0):
        base = {"resnet": 0.93, "vit": 0.91}[model]
        return {"acc": base + seed / 1000}

    for m in ("resnet", "vit"):
        for s in range(5):
            evaluate("cifar", m, seed=s)
'''


@pytest.fixture
def proj(project):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n'
                                '[metrics]\n"*.acc" = { fmt = ".1pct", better = "higher" }\n')
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    project.write("vouch_values.py", "import vouch\nvouch.expect('imagenet.vit.acc', desc='ViT on "
                                     "ImageNet', producer='python imagenet.py')\n")
    project.write("paper/main.tex", "\\documentclass{article}\\usepackage{vouch}\\begin{document}\n"
                  "ResNet \\vouch{evaluate.cifar.resnet.acc}; ImageNet \\vouch{imagenet.vit.acc}.\n"
                  "\\end{document}\n")
    build(Config.load(project.root))
    return project


def rpc(server, method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def call(server, name, **args):
    got = rpc(server, "tools/call", {"name": name, "arguments": args})["result"]
    return got, json.loads(got["content"][0]["text"])


def test_handshake_and_listing(proj):
    s = Server(proj.root)
    init = rpc(s, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}})["result"]
    assert init["protocolVersion"] == "2025-03-26"                 # a version we speak: echoed
    assert init["serverInfo"]["name"] == "vouch" and "Never type a" in init["instructions"]
    assert set(init["capabilities"]) == {"tools", "resources"}
    assert rpc(s, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == \
        "2025-06-18"                                               # otherwise: our newest
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in rpc(s, "tools/list")["result"]["tools"]]
    assert names == ["search_values", "get_value", "cite", "compare", "list_pending",
                     "list_changes", "check", "trace"]
    assert "ack" not in " ".join(names) and "accept" not in " ".join(names)
    read_only = {t["name"] for t in TOOLS if t["annotations"]["readOnlyHint"]}
    assert read_only == set(names) - {"compare"}
    assert rpc(s, "ping")["result"] == {}
    assert rpc(s, "resources/list")["result"]["resources"][0]["uri"] == "vouch://catalog"
    cat = rpc(s, "resources/read", {"uri": "vouch://catalog"})["result"]["contents"][0]
    assert cat["mimeType"] == "text/markdown" and "# vouch catalog" in cat["text"]
    assert rpc(s, "resources/read", {"uri": "vouch://nope"})["error"]["code"] == -32002
    assert rpc(s, "bogus")["error"]["code"] == -32601
    assert s.handle({"jsonrpc": "2.0", "id": 9, "result": {}}) is None       # a stray response
    assert s.handle({"id": 1}) == {"jsonrpc": "2.0", "id": 1,
                                   "error": {"code": -32600, "message": "Invalid Request"}}


def test_tools_answer_from_the_project(proj):
    s = Server(proj.root)
    res, data = call(s, "search_values", query="vit accuracy", limit=3)
    assert not res["isError"] and data["results"][0]["key"] == "evaluate.cifar.vit.acc"
    assert res["structuredContent"] == data
    _, data = call(s, "get_value", key="evaluate.cifar.resnet.acc")
    assert data["value"] == "93.2 ± 0.2%" and data["call"]["text"].startswith("evaluate(")
    assert data["runs"][0]["run"] == "exp" and data["cited_at"] == ["paper/main.tex:2"]
    assert data["subfields"]["evaluate.cifar.resnet.acc.mean"] == "93.2%"
    _, data = call(s, "cite", key="evaluate.cifar.resnet.acc", fmt=".2pct")
    assert data["snippets"] == [{"latex": "\\vouch[.2pct]{evaluate.cifar.resnet.acc}",
                                 "renders": "93.20 ± 0.16%", "fmt": ".2pct"}]
    _, data = call(s, "compare", a="evaluate.cifar.resnet.acc", b="evaluate.cifar.vit.acc")
    assert data["winner"] == "evaluate.cifar.resnet.acc" and "written" not in data
    _, data = call(s, "list_pending")
    assert data["owed"][0]["key"] == "imagenet.vit.acc" and data["owed"][0]["producer"] == \
        "python imagenet.py"
    _, data = call(s, "list_changes")
    assert data == {"changes": []}
    _, data = call(s, "check", strict=True)
    assert data["ok"] is False and [i["check"] for i in data["issues"]] == ["pending"]
    _, data = call(s, "trace", target="paper/main.tex:2")
    assert [c["key"] for c in data["citations"]] == ["evaluate.cifar.resnet.acc", "imagenet.vit.acc"]
    _, data = call(s, "trace", target="exp.py")
    assert data["runs"][0]["run"] == "exp"
    # compare(write=true) is the one tool that writes
    _, data = call(s, "compare", a="evaluate.cifar.resnet.acc", b="evaluate.cifar.vit.acc", write=True)
    assert data["written"] == "vouch_values.py" and "resnet_vs_vit" in \
        (proj.root / "vouch_values.py").read_text(encoding="utf-8")


def test_tool_errors_go_to_the_model(proj):
    s = Server(proj.root)
    res, data = call(s, "get_value", key="evaluate.cifar.resnt.acc")
    assert res["isError"] and "did you mean evaluate.cifar.resnet.acc" in data["error"]
    res, data = call(s, "cite")
    assert res["isError"] and "missing ['key']" in data["error"]
    res, data = call(s, "trace", target="nowhere")
    assert res["isError"]
    assert rpc(s, "tools/call", {"name": "ack", "arguments": {}})["error"]["code"] == -32602
    elsewhere = Server(proj.root.parent)                 # no vouch.toml there
    res, data = call(elsewhere, "search_values", query="x")
    assert res["isError"] and "no vouch.toml" in data["error"]


def test_stdio_framing(proj):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18"}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             "", "not json",
             json.dumps([{"jsonrpc": "2.0", "id": 2, "method": "ping"},
                         {"jsonrpc": "2.0", "method": "notifications/x"}])]
    out = io.StringIO()
    serve(proj.root, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
    replies = [json.loads(ln) for ln in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies[:2]] == [1, None]
    assert replies[1]["error"]["code"] == -32700
    assert replies[2] == [{"jsonrpc": "2.0", "id": 2, "result": {}}]


def test_a_real_subprocess(proj):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "cite", "arguments": {"key": "evaluate.cifar.vit.acc"}}}]
    proc = subprocess.run([sys.executable, "-m", "vouch", "mcp", "--root", str(proj.root)],
                          input="\n".join(json.dumps(m) for m in msgs) + "\n",
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    replies = [json.loads(ln) for ln in proj_lines(proj, proc.stdout)]
    assert replies[0]["result"]["serverInfo"]["name"] == "vouch"
    cite = json.loads(replies[1]["result"]["content"][0]["text"])
    assert cite["snippets"][0]["latex"] == "\\vouch{evaluate.cifar.vit.acc}"


def proj_lines(_proj, text):
    return [ln for ln in text.splitlines() if ln.strip()]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_the_official_client_can_use_it(proj, mode):
    mcp = pytest.importorskip("mcp")
    import asyncio

    async def session():
        params = mcp.StdioServerParameters(command=sys.executable,
                                           args=["-m", "vouch", "mcp", "--root", str(proj.root)])
        async with mcp.Client(params, mode=mode) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools.tools}
            found = await client.call_tool("search_values", {"query": "resnet accuracy", "limit": 2})
            catalog = await client.read_resource("vouch://catalog")
            return names, found, catalog
    names, found, catalog = asyncio.run(session())
    assert {"search_values", "cite", "check"} <= names
    assert not getattr(found, "is_error", getattr(found, "isError", False))
    assert json.loads(found.content[0].text)["results"][0]["key"] == "evaluate.cifar.resnet.acc"
    assert "# vouch catalog" in catalog.contents[0].text


def test_trace_json_and_mcp_registration(proj, capsys, monkeypatch):
    from vouch import cli
    monkeypatch.chdir(proj.root)
    assert cli.main(["trace", "evaluate.cifar.vit.acc", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["command"] == "trace" and got["type"] == "key" and got["origin"] == "exp"
    assert cli.main(["trace", "nope", "--json"]) == 1
    capsys.readouterr()
    monkeypatch.setattr("vouch.agents.shutil.which", lambda name: "vouch")
    assert cli.main(["init", "--agents", "mcp", "--yes"]) == 0
    reg = json.loads((proj.root / ".mcp.json").read_text(encoding="utf-8"))
    assert reg == {"mcpServers": {"vouch": {"command": "vouch", "args": ["mcp"]}}}
    assert not (proj.root / ".claude").exists()          # only the part asked for
