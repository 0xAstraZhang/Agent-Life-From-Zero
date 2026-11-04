from pathlib import Path
import sys
import time

import pytest

from minicode.mcp import MCPClient, MCPError
from minicode.permissions import PermissionRunner
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall

DEMO = Path(__file__).resolve().parents[1] / "examples" / "mcp_demo_server.py"


def test_stdio_handshake_call_errors_and_close(tmp_path):
    with MCPClient([sys.executable, str(DEMO)], name="demo") as client:
        tools = client.list_tools()
        assert len(tools) == 1 and tools[0].spec.name == "mcp__demo__echo"
        assert not tools[0].read_only  # 服务器的 readOnlyHint 不决定本地权限。
        runner = ToolRunner(tools, ToolContext(tmp_path))
        result = runner.run(ToolCall("call-id", tools[0].spec.name, {"text": "hello"}))
        assert result.call_id == "call-id" and result.content == "hello" and not result.is_error
        result = runner.run(ToolCall("error-id", tools[0].spec.name, {"text": "error: failure"}))
        assert result.call_id == "error-id" and result.is_error
        denied = PermissionRunner(runner).run(ToolCall("denied", tools[0].spec.name, {"text": "x"}))
        assert denied.is_error
    assert client.proc.poll() is not None
    with pytest.raises(MCPError, match="关闭"):
        client.list_tools()


def test_version_mismatch_closes_process(tmp_path):
    server = tmp_path / "bad_version.py"
    server.write_text('''import json, sys
for line in sys.stdin:
    req = json.loads(line)
    if "id" in req:
        print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":{"protocolVersion":"unknown","capabilities":{"tools":{}}}}), flush=True)
''')
    client = MCPClient([sys.executable, str(server)])
    with pytest.raises(MCPError, match="只支持"):
        with client:
            pass
    assert client.proc.poll() is not None


def test_timeout_stops_process(tmp_path):
    server = tmp_path / "hang.py"
    server.write_text("import time\ntime.sleep(20)\n")
    client = MCPClient([sys.executable, str(server)], timeout=0.1)
    with pytest.raises(MCPError, match="超时"):
        with client:
            pass
    assert client.proc.poll() is not None


def test_invalid_stdout_is_not_ignored(tmp_path):
    server = tmp_path / "noisy.py"
    server.write_text("print('logging to stdout is invalid', flush=True)\n")
    with pytest.raises(MCPError, match="stdout"):
        with MCPClient([sys.executable, str(server)]):
            pass


def test_notifications_do_not_extend_deadline(tmp_path):
    server = tmp_path / "notifications.py"
    server.write_text('''import json, sys, time
for line in sys.stdin:
    r=json.loads(line)
    if r.get("method")=="initialize":
        print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":{"protocolVersion":"2025-11-25","capabilities":{"tools":{}}}}),flush=True)
    elif r.get("method")=="tools/list":
        while True:
            print(json.dumps({"jsonrpc":"2.0","method":"notifications/progress","params":{}}),flush=True)
            time.sleep(0.001)
''')
    with MCPClient([sys.executable, str(server)], timeout=0.1) as client:
        started = time.monotonic()
        with pytest.raises(MCPError, match="超时"):
            client.list_tools()
        assert time.monotonic() - started < 2
        assert client.proc.poll() is not None


def test_pagination_and_server_request(tmp_path):
    server = tmp_path / "pages.py"
    server.write_text('''import json, sys
for line in sys.stdin:
    r=json.loads(line)
    if "method" not in r or "id" not in r: continue
    m=r["method"]
    if m=="initialize":
        value={"protocolVersion":"2025-11-25","capabilities":{"tools":{}}}
    elif m=="tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":"ping","method":"ping"}),flush=True)
        page=r.get("params",{}).get("cursor")
        value={"tools":[{"name":"b" if page else "a","inputSchema":{"type":"object"}}]}
        if not page: value["nextCursor"]="second"
    else: value={}
    print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":value}),flush=True)
''')
    with MCPClient([sys.executable, str(server)], name="pages") as client:
        assert [tool.spec.name for tool in client.list_tools()] == ["mcp__pages__a", "mcp__pages__b"]
