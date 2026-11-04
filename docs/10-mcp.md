# 第 10 章：MCP，把外部世界接进来（含浏览器自动化）

## 导读

到目前为止，minicode 的每一个工具都写在它自己的代码里。可是你的 agent 迟早要连 GitHub、数据库、公司内部的系统、浏览器……如果每接一个系统都要改 agent 的代码，这件事很快就做不下去了；而且每个 agent 产品都要为同一个系统各写一遍接入。

**MCP（Model Context Protocol）** 把这件事标准化了：外部系统的能力由一个 **MCP 服务器**提供，任何支持 MCP 的 agent（Claude Code、minicode……）作为**客户端**连上去，就能用它的工具。写一次服务器，所有客户端都能用。

学完你会有：

- `minicode/mcp_client.py`：一个从零手写的 MCP 客户端（stdio 传输），把 MCP 工具包装成 minicode 的工具。
- `labnotes_server.py`：用官方 Python SDK 写的一个 MCP 服务器。
- 接入 Playwright 的 MCP 服务器，让 agent 操作浏览器，并用第 4 章的 hook 给它加上安全边界。

## Claude Code 是怎么做的

以下内容来自 Claude Code 的 [MCP 文档](https://code.claude.com/docs/en/mcp)。

### 配置

项目级的 MCP 服务器写在项目根目录的 `.mcp.json` 里，可以提交到 git，团队共享：

```json
{
  "mcpServers": {
    "shared-server": {"type": "http", "url": "https://example.com/mcp"}
  }
}
```

传输方式有 stdio（本地启动一个进程）、HTTP（推荐用于远程服务器）和已经不推荐的 SSE。配置分三个范围：local（默认，只对你在这个项目生效）、project（`.mcp.json`，团队共享）、user（你的所有项目）。交互模式下，第一次使用项目级的服务器前会请用户确认。

### 工具命名和权限

MCP 工具在 Claude Code 里的名字是 `mcp__<服务器>__<工具>`。权限规则可以针对整个服务器（`mcp__github`），也可以针对单个工具（`mcp__github__create_issue`）。

### 工具太多怎么办

一个 MCP 服务器动辄提供几十个工具，每个工具的定义都要占上下文。Claude Code 默认开启 **MCP tool search**：会话开始时只加载工具的名字，模型需要时再通过搜索把完整定义加载进来。

### 输出太长怎么办

文档写明：MCP 工具的输出超过 10000 token 时会显示警告，默认最多保留 25000 token，可以用环境变量 `MAX_MCP_OUTPUT_TOKENS` 调整。

## MCP 在线路上长什么样

stdio 传输非常朴素：启动服务器进程，往它的 stdin 写一行 JSON，从它的 stdout 读一行 JSON。一次完整的握手和调用：

```text
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"minicode","version":"0.1"}}}
← {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"tools":{"listChanged":false},…},"protocolVersion":"2025-06-18","serverInfo":{"name":"labnotes","version":""}}}
→ {"jsonrpc":"2.0","method":"notifications/initialized"}
→ {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
← {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"lookup_sample","description":"查询某个样本的 OD600 读数。……","inputSchema":{"properties":{"sample":{"type":"string",…}},"required":["sample"],"type":"object"},"annotations":{"readOnlyHint":true},…}]}}
→ {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"lookup_sample","arguments":{"sample":"B"}}}
← {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"样本B 的 OD600 是 1.37"}],"isError":false,"structuredContent":{…}}}
```

这是和下面第 2 步写的服务器实际交互的记录（省略了部分字段）。

几个要点：

- 带 `id` 的是请求，必须有响应；不带 `id` 的是通知，没有响应。
- `tools/list` 返回的 `inputSchema` 就是 JSON Schema，可以直接当作给模型的参数说明。
- `annotations.readOnlyHint` 声明工具是不是只读的。Agent SDK 文档写明，标了只读的 MCP 工具可以和 Read、Grep 一样并行执行。
- 工具执行出错时，结果里 `isError` 为 true，错误信息在 `content` 里，和第 2 章的 `ToolResult(is_error=True)` 是同一个意思。

## 第 1 步：MCP 客户端

新建 `minicode/mcp_client.py`：

```python
"""一个最小的 MCP 客户端（stdio 传输），把 MCP 服务器提供的工具包装成 minicode 的 Tool。

MCP 的 stdio 传输很朴素：启动服务器进程，往它的 stdin 写一行 JSON-RPC 请求，从 stdout 读一行响应。
一次完整的握手和调用：
    → initialize            ← 服务器的名字、版本、能力
    → notifications/initialized（通知，没有响应）
    → tools/list            ← 工具列表：name、description、inputSchema、annotations
    → tools/call            ← 结果：content 列表 + isError
"""
import json
import os
import queue
import re
import subprocess
import threading
from pathlib import Path

from .settings import CONFIG_DIR
from .tool import Tool, ToolResult

PROTOCOL_VERSION = "2025-06-18"
MAX_OUTPUT_CHARS = 25_000 * 3     # Claude Code 默认把 MCP 工具输出限制在 25000 token 左右


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, name: str, command: str, args=(), env: dict | None = None, cwd=None):
        self.name = name
        self.proc = subprocess.Popen([command, *args], cwd=cwd, text=True, encoding="utf-8",
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env={**os.environ, **(env or {})})
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:          # 后台线程一直读，主线程按 id 取自己的响应
            self._lines.put(line)
        self._lines.put(None)                  # 进程退出了

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        with self._lock:                       # 一次只发一个请求，简单可靠
            self._next_id += 1
            rid = self._next_id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            while True:
                try:
                    line = self._lines.get(timeout=timeout)
                except queue.Empty:
                    raise MCPError(f"{self.name}：{method} 超过 {timeout} 秒没有响应")
                if line is None:
                    raise MCPError(f"{self.name}：服务器进程已退出")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue                   # 不是 JSON 的行（有的服务器会往 stdout 打日志），跳过
                if msg.get("id") == rid and "method" not in msg:
                    if "error" in msg:
                        raise MCPError(f"{self.name}：{msg['error'].get('message')}")
                    return msg.get("result", {})
                if "method" in msg and "id" in msg:    # 服务器发来的请求（比如 ping），回一个空结果
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    def start(self) -> dict:
        info = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                           "clientInfo": {"name": "minicode", "version": "0.1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return info

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += result.get("tools", [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments}, timeout=120)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


class MCPTool(Tool):
    """把一个 MCP 工具包装成普通 Tool，名字是 mcp__服务器__工具，和 Claude Code 一样。"""

    def __init__(self, client: MCPClient, spec: dict):
        self.client, self.remote_name = client, spec["name"]
        self.name = f"mcp__{_safe(client.name)}__{_safe(spec['name'])}"[:64]
        self.description = spec.get("description") or spec["name"]
        self.parameters = spec.get("inputSchema") or {"type": "object", "properties": {}}
        self.read_only = bool((spec.get("annotations") or {}).get("readOnlyHint"))

    @property
    def needs_permission(self):
        return True       # 外部服务器的工具一律先问，靠 allow 规则放行信得过的

    def call(self, args, ctx):
        result = self.client.call_tool(self.remote_name, args)
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item["text"])
            else:
                parts.append(f"[{item.get('type')} 类型的内容，minicode 暂不支持显示]")
        text = "\n".join(parts) or "（工具没有返回内容）"
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + f"\n...[输出过长，已截断，原长 {len(text)} 字符]"
        return ToolResult(text, is_error=bool(result.get("isError")))


def load_mcp_tools(cwd: Path) -> tuple[list[MCPClient], list[Tool]]:
    """读 .minicode/mcp.json（格式同 Claude Code 的 .mcp.json），启动每个服务器，返回 (客户端, 工具)。"""
    config_path = cwd / CONFIG_DIR / "mcp.json"
    if not config_path.exists():
        return [], []
    servers = json.loads(config_path.read_text(encoding="utf-8")).get("mcpServers", {})
    clients, tools = [], []
    for name, spec in servers.items():
        if spec.get("type", "stdio") != "stdio":
            print(f"[mcp] 跳过 {name}：minicode 只实现了 stdio 传输")
            continue
        try:
            client = MCPClient(name, spec["command"], spec.get("args", []), spec.get("env"), cwd)
            client.start()
            tools += [MCPTool(client, t) for t in client.list_tools()]
            clients.append(client)
        except (OSError, MCPError) as e:
            print(f"[mcp] 启动 {name} 失败：{e}")
    return clients, tools
```

`MCPClient` 用一个后台线程不停地读服务器的 stdout，把每一行放进队列；`request` 发出请求后，从队列里找 `id` 对得上的那一条响应。这样即使服务器在响应之前插进来一条通知，或者往 stdout 打了一行日志，也不会把对话搞乱。

`MCPTool` 是一个适配器：它把 MCP 工具的名字、描述、`inputSchema`、`readOnlyHint` 翻译成第 2 章的 `Tool` 接口，主循环完全不知道它背后是一个外部进程。注意它的 `needs_permission` 永远是 True：外部服务器的工具一律先问，信得过的再用 allow 规则放行。

## 第 2 步：写一个 MCP 服务器

服务器端用官方的 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：

```bash
uv add mcp
```

新建 `labnotes_server.py`（放在 `agent-life-from-zero/` 下，和 `minicode/` 平级）：

```python
"""一个最小的 MCP 服务器：把实验记录查询做成工具。用官方 Python SDK（mcp 2.x）编写。

单独运行它不会有任何输出：它在等 stdin 上的 JSON-RPC 消息。由 minicode 启动它。
"""
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

server = MCPServer("labnotes")
SAMPLES = {"A": 0.82, "B": 1.37, "C": 0.95}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lookup_sample(sample: str) -> str:
    """查询某个样本的 OD600 读数。sample 是样本编号，例如 A。"""
    if sample not in SAMPLES:
        raise ToolError(f"没有样本 {sample}，现有样本：{', '.join(SAMPLES)}")   # 预料之中的错误，原样告诉模型
    return f"样本{sample} 的 OD600 是 {SAMPLES[sample]}"


if __name__ == "__main__":
    server.run()      # 默认走 stdio 传输
```

> 这里用的是 mcp 2.x 的写法：服务器类叫 `MCPServer`。网上很多教程用的是 1.x 的 `FastMCP`，在 2.x 里已经改名。

两种错误的区别值得注意：抛 `ToolError` 时，错误信息原样交给模型（“没有样本 Z，现有样本：A, B, C”），模型能据此改正；抛其他异常时，SDK 认为是服务器的 bug，只告诉模型 “Error executing tool lookup_sample”，不泄露内部细节。这和第 2 章 minicode 自己区分 `ToolError` 和普通异常是同一个思路。

## 第 3 步：接进 app.py

在 `minicode/app.py` 里加一个导入：

```python
from .mcp_client import load_mcp_tools
```

在 skill 那几行下面加：

```python
    mcp_clients, mcp_tools = load_mcp_tools(cwd) if with_mcp else ([], [])    # 第 10 章
    tools += mcp_tools
```

在 `agent.skills = skills` 下面加一行，退出时 `cli.py` 会用它关掉服务器进程：

```python
    agent.mcp_clients = mcp_clients                                           # 第 10 章：退出时关掉服务器
```

改完的完整文件：

```python
"""组装：读配置、建工具、接上各个接缝，得到一个能用的 Agent。对应 Claude Code 启动时做的事。"""
import os
from pathlib import Path

from .agent import Agent
from .compact import Compactor
from .context import auto_memory_file, build_system_prompt, memory_context
from .hooks import HookRunner
from .llm import LLM
from .mcp_client import load_mcp_tools
from .permissions import PermissionEngine, parse_rule
from .session import Session
from .settings import CONFIG_DIR, load_settings
from .skills import load_skills
from .subagents import load_agent_defs
from .tools import core_tools
from .tools.planning import ExitPlanMode, TodoWrite, plan_mode_reminder, todo_reminder
from .tools.skill import SkillTool
from .tools.task import AgentTool
from .tools.web import WebFetch


def build_agent(cwd=".", *, llm=None, mode=None, allowed_tools=(), disallowed_tools=(),
                resume=None, continue_session=False, max_turns=50, max_budget_usd=None,
                ask_user=None, home=None, with_mcp=True) -> Agent:
    cwd = Path(cwd).resolve()
    settings = load_settings(cwd, home)                                       # 第 3 章
    llm = llm or LLM(model=settings.get("model"))
    perms = settings["permissions"]
    permissions = PermissionEngine(
        mode or perms.get("defaultMode", "default"),
        allow=perms["allow"] + list(allowed_tools), ask=perms["ask"], deny=perms["deny"] + list(disallowed_tools),
        workdirs=[cwd, auto_memory_file(cwd, home).parent, *perms.get("additionalDirectories", [])],
        persist_to=cwd / CONFIG_DIR / "settings.local.json")

    tools = core_tools() + [WebFetch()]                                       # 第 2、5 章
    tools += [TodoWrite(), ExitPlanMode()]                                    # 第 7 章
    tools.append(AgentTool(load_agent_defs(cwd, home)))                       # 第 8 章
    skills = load_skills(cwd, home)                                           # 第 9 章
    if skills:
        tools.append(SkillTool(skills))
    mcp_clients, mcp_tools = load_mcp_tools(cwd) if with_mcp else ([], [])    # 第 10 章
    tools += mcp_tools
    removed = {parse_rule(r)[0] for r in permissions.rules["deny"] if parse_rule(r)[1] is None}
    tools = [t for t in tools if t.name not in removed]       # deny 里只写工具名 = 让模型根本看不到这个工具

    if resume or continue_session:                                            # 第 6 章
        session, history = Session.resume(cwd, resume, home)
    else:
        session, history = Session(cwd, home=home), []
    hooks = HookRunner(settings["hooks"], cwd, session.id, str(session.path),
                       lambda: permissions.mode) if settings["hooks"] else None   # 第 4 章
    memory = memory_context(cwd, home)                                        # 第 5 章
    agent = Agent(llm, tools, build_system_prompt(cwd, llm.model), cwd=cwd,   # 第 5 章
                  max_turns=max_turns, permissions=permissions, hooks=hooks, session=session,
                  compactor=Compactor(llm, int(os.getenv("MINICODE_CONTEXT_WINDOW", 128_000)), memory=memory),
                  reminders=[plan_mode_reminder, todo_reminder], ask_user=ask_user)   # 第 7 章
    agent.skills = skills                                                     # 第 9 章：给 /技能名 命令用
    agent.mcp_clients = mcp_clients                                           # 第 10 章：退出时关掉服务器

    if history:
        agent.messages = [agent.messages[0], *history[1:]]    # 换成最新的系统提示词
        agent.repair()                                        # 上次如果停在工具执行中间，补上结果
    else:
        session.append(agent.messages[0])
        agent.pending_context.append(memory)
    if hooks and (extra := hooks.session_start("resume" if history else "startup")):
        agent.pending_context.append(extra)
    return agent
```

## 跑起来：接上自己的服务器

在 playground 里配置服务器。`command` 用当前 Python 解释器的绝对路径，确保服务器能找到 `mcp` 包：

```bash
cp labnotes_server.py playground/
```

```bash
uv run python -c "import json, sys; print(json.dumps({'mcpServers': {'labnotes': {'command': sys.executable, 'args': ['labnotes_server.py']}}}, indent=2))" > playground/.minicode/mcp.json
```

```bash
cat playground/.minicode/mcp.json
```

启动后问 `样本B 和样本Z 的 OD600 分别是多少？`。你会看到：

- 工具名是 `mcp__labnotes__lookup_sample`；
- 两次调用在同一次回复里发出（只读工具，并行）；
- 第一次调用要你确认（外部工具一律先问），选 `a` 会生成一条 `mcp__labnotes__lookup_sample` 规则；
- 查样本Z 时，模型收到“没有样本 Z，现有样本：A, B, C”，并如实告诉你。

## 跑起来：让 agent 操作浏览器

浏览器 agent 不需要你从零写：微软的 [Playwright MCP](https://github.com/microsoft/playwright-mcp) 把浏览器操作做成了一组 MCP 工具（打开网页、获取页面的无障碍快照、点击、输入……）。它用页面的结构化快照而不是截图来描述页面，模型通过快照里的元素引用来点击和输入。

> 本节的配置需要 Node.js，`npx` 会下载 Playwright MCP 和浏览器。本教程的离线测试只验证了 minicode 的 MCP 客户端（用上面的 labnotes 服务器），没有在测试环境里实际运行 Playwright MCP。工具名和参数以 Playwright MCP 当前的 README 为准。

**先划安全边界，再接浏览器。** 浏览器 agent 读的是不受你控制的网页，网页里可以藏任何“指令”；它的动作（点击、提交）可能产生真实的后果。所以在接入之前，先写一个 PreToolUse hook：只允许打开白名单里的网站，不点付款、删除、下单一类的按钮，不上传本地文件。

```bash
mkdir -p playground/.minicode/hooks
```

新建 `playground/.minicode/hooks/browser_guard.py`：

```python
"""PreToolUse hook：浏览器只能打开白名单里的网站，并且不点“付款、删除、提交订单”一类按钮。

在 .minicode/settings.json 里配置：
    {"hooks": {"PreToolUse": [{"matcher": "mcp__playwright__.*",
                               "hooks": [{"type": "command", "command": "python .minicode/hooks/browser_guard.py"}]}]}}
"""
import json
import sys
from urllib.parse import urlparse

ALLOWED_HOSTS = {"example.com", "en.wikipedia.org", "zh.wikipedia.org", "docs.python.org"}
RISKY_WORDS = ("付款", "支付", "购买", "下单", "删除", "注销", "提交订单", "pay", "buy", "delete", "checkout")

data = json.load(sys.stdin)
tool, args = data["tool_name"], data["tool_input"]

if tool.endswith("browser_navigate"):
    host = (urlparse(args.get("url", "")).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        print(f"{host or '这个地址'} 不在允许访问的网站名单里：{', '.join(sorted(ALLOWED_HOSTS))}", file=sys.stderr)
        sys.exit(2)

if tool.endswith(("browser_click", "browser_type")):
    target = json.dumps(args, ensure_ascii=False).lower()
    if any(word in target for word in RISKY_WORDS):
        print("这个操作可能产生真实后果（付款、删除、下单），浏览器 agent 不做，请用户自己处理。", file=sys.stderr)
        sys.exit(2)

if tool.endswith("browser_file_upload"):
    print("不允许通过浏览器上传本地文件。", file=sys.stderr)
    sys.exit(2)
```

把 Playwright MCP 加进 `playground/.minicode/mcp.json`（和 labnotes 并列）：

```json
{
  "mcpServers": {
    "labnotes": {"command": "（保持原样）", "args": ["labnotes_server.py"]},
    "playwright": {"command": "npx", "args": ["@playwright/mcp@latest", "--headless", "--isolated"]}
  }
}
```

把 hook 加进 `playground/.minicode/settings.json` 的 `hooks.PreToolUse`：

```json
{"matcher": "mcp__playwright__.*", "hooks": [{"type": "command", "command": "python .minicode/hooks/browser_guard.py"}]}
```

`--isolated` 让浏览器使用一个干净的临时配置，不带你平时浏览器里的登录状态；`--headless` 不弹出窗口。然后试试：

```text
> 打开 https://en.wikipedia.org/wiki/Optical_density ，用三句话总结 OD 的定义，并给出你读到的原文
```

再试一个白名单之外的网站，观察 hook 怎么拦下 `browser_navigate`。

几条浏览器 agent 的底线：

- **只操作公开网页，不登录你的真实账号。** 登录态下，一条藏在网页里的注入指令就可能让 agent 以你的身份行事。
- **会产生真实后果的动作交给人。** 付款、提交表单、发消息、删除，即使 agent 能做，也应该停下来让用户自己点。
- **遵守网站的规则。** 不绕过验证码、不违反服务条款、不高频抓取。
- **留下记录。** minicode 的会话记录里有每一次浏览器操作；需要更细的复盘时，可以让 Playwright MCP 保存截图或操作轨迹（见它的 README）。

## 离线测试

新建 `tests/test_ch10.py`，它会用你刚写的 `labnotes_server.py` 启动一个真实的 MCP 服务器：

```python
import json
import shutil
import sys
from pathlib import Path

from minicode.agent import Agent
from minicode.mcp_client import load_mcp_tools
from minicode.permissions import PermissionEngine
from minicode.testing import FakeLLM, call

SERVER = Path(__file__).resolve().parent.parent / "labnotes_server.py"


def test_mcp_tools_through_agent(tmp_path):
    shutil.copy(SERVER, tmp_path / "labnotes_server.py")
    (tmp_path / ".minicode").mkdir()
    (tmp_path / ".minicode/mcp.json").write_text(json.dumps(
        {"mcpServers": {"labnotes": {"command": sys.executable, "args": ["labnotes_server.py"]}}}))
    clients, tools = load_mcp_tools(tmp_path)
    try:
        assert [t.name for t in tools] == ["mcp__labnotes__lookup_sample"] and tools[0].read_only
        llm = FakeLLM([[call("mcp__labnotes__lookup_sample", sample="B"),
                        call("mcp__labnotes__lookup_sample", sample="Z")], "B 是 1.37"])
        agent = Agent(llm, tools, "s", cwd=tmp_path,
                      permissions=PermissionEngine(allow=["mcp__labnotes"], workdirs=[tmp_path]))
        results = [e for e in agent.query("样本B？") if e["type"] == "tool_result"]
        assert "1.37" in results[0]["content"]
        assert results[1]["is_error"] and "没有样本 Z" in results[1]["content"]
    finally:
        for c in clients:
            c.close()
```

```bash
uv run python -m pytest tests -q
```

## MCP 之外：A2A 和 ACP

MCP 解决的是 “agent 连接工具和数据”。另外两个协议解决别的连接问题，知道它们存在就好：

| 协议 | 连接什么 | 例子 |
| --- | --- | --- |
| [MCP](https://modelcontextprotocol.io/) | agent ↔ 工具、数据源 | minicode 调用 labnotes 服务器 |
| [A2A](https://a2a-protocol.org/latest/specification/) | agent ↔ 别的 agent | 你的 agent 把子任务交给另一家公司的 agent |
| [ACP](https://agentclientprotocol.com/) | agent ↔ 编辑器、IDE 等宿主应用 | 在编辑器里直接使用某个 coding agent |

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| 传输 | stdio、HTTP、SSE，还有 WebSocket | 只有 stdio |
| 配置 | `.mcp.json` 加上 local、user 两个范围，可以用 `claude mcp add` 管理 | 只有 `.minicode/mcp.json` |
| 认证 | 支持远程服务器的 OAuth | 没有 |
| MCP 的其他能力 | 资源（resources）、提示词（prompts）等 | 只用了工具 |
| 工具很多时 | 默认按需搜索工具定义 | 全部工具定义一开始就发给模型 |
| 首次使用确认 | 项目级服务器第一次使用前要用户确认 | 没有（`mcp.json` 里写什么就启动什么） |
| 输出限制 | 约 25000 token，可配置 | 约 75000 字符，写死在代码里 |

## 自检清单

- [ ] 能写出 MCP 握手和一次工具调用的消息顺序。
- [ ] 能解释为什么客户端要用后台线程读 stdout、按 `id` 匹配响应。
- [ ] 能说出服务器端 `ToolError` 和普通异常的区别。
- [ ] 能为浏览器 agent 列出至少三条安全边界，并说出各自用什么机制实现（权限规则、hook、启动参数）。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `[mcp] 启动 labnotes 失败：服务器进程已退出` | 服务器启动就崩了，最常见的是找不到 `mcp` 包 | 单独运行 `uv run python labnotes_server.py` 看报错；`command` 用装了 `mcp` 的那个 Python |
| `ModuleNotFoundError: mcp.server.fastmcp` | 照着 1.x 的教程写的 | 2.x 改用 `from mcp.server.mcpserver import MCPServer` |
| 服务器卡住没有响应 | 服务器往 stdout 打印了调试信息，或者在等别的输入 | stdio 服务器的日志只能写到 stderr |
| 模型说调用失败，但看不到原因 | 服务器抛了普通异常，SDK 隐藏了细节 | 预料之中的错误改抛 `ToolError` |
| 工具一多，模型变笨、变贵 | 几十个工具定义塞满了上下文 | 只启用需要的服务器；或者实现进阶练习 1 |

## 进阶练习

1. **实现工具搜索。** 工具超过 20 个时，只把名字列在一个 `ToolSearch` 工具的说明里；模型调用 `ToolSearch` 后，把匹配的工具定义加进下一次请求的 `tools`。
2. 给 `MCPClient` 加 HTTP 传输（Streamable HTTP）。
3. 实现“首次使用确认”：启动 `mcp.json` 里没确认过的服务器前，列出它的命令行，请用户确认，结果记在 `settings.local.json`。
4. 把第 2 章的 Grep 做成一个独立的 MCP 服务器，让 minicode 通过 MCP 使用它。比较两种方式的优缺点。

## 资料来源

- [Model Context Protocol 规范](https://modelcontextprotocol.io/specification/2025-06-18)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Claude Code: Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp)
- [Claude Code: Configure permissions](https://code.claude.com/docs/en/permissions)：MCP 工具的规则写法
- [Playwright MCP](https://github.com/microsoft/playwright-mcp)
- [How the agent loop works（Agent SDK）](https://code.claude.com/docs/en/agent-sdk/agent-loop)：只读 MCP 工具的并行执行
