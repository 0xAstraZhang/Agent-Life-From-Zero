# 第 17 章：用 bubblewrap 隔离 Bash

上一章：[交付](16-shipping.md) · [目录](README.md) · [源码参考](references.md)

## 本章的完整累积目录

先回到教程仓库根目录导出本章，再进入导出目录：

```bash
uv run python scripts/check_tutorial.py --chapter 17 --export /tmp/minicode-ch17
cd /tmp/minicode-ch17
uv sync
```

导出目录包含第 00–17 章的全部文件。本章新增 [sandbox.py](../minicode/sandbox.py)、[实验入口](../examples/17_sandbox.py)和[本章测试](../tests/test_sandbox.py)，并替换 [cli.py](../minicode/cli.py)，增加三个参数。第 16 章的 CLI 保存在[阶段快照](../lessons/stages/16/minicode/cli.py)里。其他模块不变。

## 这一章的结果

第 7 章的权限只决定一次调用执行还是不执行。用户一旦允许 Bash，命令就拥有当前用户的全部能力：可以写工作区外的文件、联网、读取环境变量里的 API Key。第 6 章的路径检查只约束 Read、Write、Edit，管不到 shell。

本章在 Linux 上用 [bubblewrap](https://github.com/containers/bubblewrap)（命令名 `bwrap`）给 Bash 加第二道边界。开启 `--sandbox` 后，规则如下：

| 方面 | 规则 |
| --- | --- |
| 写文件 | 只有工作区和显式指定的目录可写，其余整个文件系统只读 |
| 受保护路径 | `.git`、`.minicode`，以及本次使用的会话文件、项目说明、技能目录保持只读；Write、Edit 同样拒绝 |
| 网络 | 默认断开；`--sandbox-network` 整体开启 |
| `/tmp` | 每条命令一个独立的空目录，命令结束后丢弃 |
| 环境变量 | 名称含 KEY、TOKEN、SECRET、PASSWORD、CREDENTIAL 的变量，以及 `--api-key-env` 指定的变量，都被移除 |
| 进程 | 命令结束或超时后，它启动的后台进程一起结束 |
| 不可用时 | 启动时检查，失败就退出，不会悄悄在没有沙箱的情况下运行 |

审批规则不变。沙箱是第二道防线，不替代审批，但它降低了"允许 Bash"的风险。例如无人值守运行时，可以考虑 `--sandbox --allow-tool Bash`。

本章不做：macOS 和 Windows 支持、按域名放行网络、限制读取、隔离 MCP 子进程、被拦截后自动请求放开。原因见本章最后的[边界](#边界)。

## 第一步：确认 bwrap 能用

```bash
sudo apt install bubblewrap      # Debian / Ubuntu
sudo dnf install bubblewrap      # Fedora
bwrap --version
uv run python examples/17_sandbox.py
```

bwrap 依赖内核的非特权用户命名空间。装上了不代表能用：Docker 的默认配置、一些发行版的安全策略都可能禁止它。所以 `Sandbox.check()` 不只是查找 `bwrap` 命令，而是用真实参数运行一次 `true`：

```python
result = subprocess.run(self.argv("true", root), stdin=subprocess.DEVNULL,
                        capture_output=True, text=True, timeout=10)
if result.returncode != 0:
    raise SandboxUnavailable(...)
```

检查失败时，CLI 打印原因，以退出码 2 结束。用户明确要求了沙箱，环境不支持时就应该停下，而不是在没有沙箱的情况下继续。

## 第二步：把规则翻译成 bwrap 参数

`Sandbox.argv()` 把一条 shell 命令变成 bwrap 的参数列表。bwrap 按顺序处理挂载，后面的挂载会盖住前面同一路径的挂载，所以参数顺序本身就是规则：

```python
argv = ["bwrap", "--ro-bind", "/", "/",          # 1. 整个文件系统只读
        "--dev", "/dev", "--proc", "/proc",
        "--tmpfs", "/tmp"]                       # 2. 换成空的 /tmp
for path in [root, *writable]:
    argv += ["--bind", str(path), str(path)]     # 3. 工作区重新设为可写
for path in self.protected_paths(root):
    if path.exists():
        argv += ["--ro-bind", str(path), str(path)]  # 4. 工作区里的敏感路径改回只读
```

| 参数 | 作用 |
| --- | --- |
| `--ro-bind / /` | 命令看到的仍是原来的系统，程序和库都能用，但都不能改 |
| `--tmpfs /tmp` | 盖住原来的 `/tmp`；工作区如果在 `/tmp` 下，第 3 步会在它上面重新挂出来 |
| `--bind root root` | 工作区可写；沙箱内外路径相同，报错里的路径不用转换 |
| `--ro-bind .git .git` | 写入报 `Read-only file system`；`mv .git` 也会失败，因为挂载点不能移动 |
| `--unsetenv NAME` | 移除密钥变量 |
| `--unshare-pid --die-with-parent` | 独立的 PID 命名空间；bwrap 退出时，里面的进程全部结束 |
| `--new-session` | 脱离原来的终端会话，命令不能向用户的终端注入输入 |
| `--unshare-net` | 独立的网络命名空间，只有回环接口 |
| `--chdir root -- /bin/sh -c 命令` | 在工作区里用 `/bin/sh` 执行原命令 |

受保护路径有两个细节：

- `protected_paths()` 先 `resolve()` 再比较，只保留工作区里的路径。工作区外本来就是只读。
- bwrap 只能挂载已经存在的路径。`.minicode` 还不存在时，命令可以创建它。Write、Edit 的检查只看路径，不受这个限制（见第四步）。

## 第三步：包装 Bash，而不是重写它

第 6 章的 Bash 工具已经处理了超时、输出截断和进程组清理。沙箱只需要改写命令字符串：

```python
def execute(args, ctx):
    command = "exec " + shlex.join(sandbox.argv(args["command"], ctx.cwd))
    return inner(dict(args, command=command), ctx)
```

`shlex.join` 给每个参数加上正确的引号。用户命令作为一个完整参数交给沙箱里的 `/bin/sh -c`，其中的引号和 `$` 不会被外层 shell 展开。`exec` 让外层 shell 直接替换成 bwrap，超时后原来的 `killpg` 结束的就是 bwrap 本身。

这里顺带补上了第 6 章的一个缺口。原来超时后会结束整个进程组，但后台进程只要调用 `setsid` 就能离开进程组，在超时后继续运行。沙箱里的进程都在同一个 PID 命名空间中，bwrap 一退出，不管属于哪个进程组都会结束。正常结束的命令也一样：`cmd &` 留下的后台进程会随命令一起结束。

`sandbox_tools()` 返回一个新的工具列表：Bash 换成包装后的版本，Write、Edit 加上路径检查，其他工具原样保留。传入的工具对象不会被修改。

## 第四步：Write 和 Edit 使用同一份受保护路径

Read、Write、Edit 在 minicode 自己的进程里执行，bwrap 管不到它们。如果只保护 Bash，模型改用 Write 写 `.git/hooks/pre-commit`，保护就被绕过了：用户下次执行 `git commit` 时，这个脚本会在沙箱外运行。

```python
def execute(args, ctx):
    path = resolve_inside(ctx, args["path"])  # 先解析 .. 和符号链接
    if sandbox.protects(path, ctx.cwd):
        return ToolResult("", f"{relative} 是受保护路径，沙箱模式下不允许修改", True)
    return inner(args, ctx)
```

因为先解析再比较，`alias/config`（`alias` 是指向 `.git` 的符号链接）会被拒绝，`.git/../ok.txt` 会被允许。`protects()` 用 `Path.is_relative_to` 按路径层级比较，`.gitignore` 不会被误认为在 `.git` 里面。

CLI 还会把本次使用的会话文件、`--instructions` 文件和 `--skills` 目录加入受保护列表，前提是它们在工作区内。这三样都会进入下一次请求的上下文。命令如果能改写它们，就能给以后的会话留下指令。

## 第五步：告诉模型规则，但不猜测拦截原因

沙箱模式下，Bash 的工具说明整个换成：

```text
执行 shell 命令，需要权限确认。命令在 bubblewrap 沙箱中运行：只有工作区可写，受保护路径（.git、.minicode）只读，网络已断开；/tmp 是本条命令独立的临时目录，结束后清空；命令结束时其后台进程一并终止。
```

原来的说明里有一句"cwd 不是沙箱"，开启沙箱后不再成立。如果直接在后面追加，模型会读到两条互相矛盾的规则，所以这里整体替换。

命令被拦截时，模型看到的是普通的错误输出，比如 `Read-only file system` 和非零退出码。minicode 不去判断这次失败是不是沙箱造成的：被拦截的写入，退出码通常是 2，和大量普通错误一样。Codex 的 [denial.rs](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/sandboxing/src/denial.rs) 也在注释里说明没有确定的判断方法，只能根据退出码和关键词推测。本章只把规则写进工具说明，由模型阅读错误信息。

## 第六步：从 CLI 开启

```bash
uv run minicode --sandbox
uv run minicode --sandbox --allow-tool Bash -p "运行测试，找出失败原因"
uv run minicode --sandbox --sandbox-writable ~/.cache/uv
uv run minicode --sandbox --sandbox-network
```

| 参数 | 用途 |
| --- | --- |
| `--sandbox` | 开启沙箱；启动时检查，不可用就以退出码 2 结束 |
| `--sandbox-network` | 沙箱内允许网络，只能整体开启 |
| `--sandbox-writable DIR` | 额外可写的目录，可以重复；只对 Bash 生效，文件工具仍限制在工作区内 |

后两个参数必须和 `--sandbox` 一起用，单独使用会报错，免得用户以为已经开了沙箱。

`run()` 在创建模型客户端之前调用 `build_sandbox()`。配置有误或沙箱不可用时，不会发出任何模型请求。

开启后常见的失败：

- `uv`、`pip` 需要写 `~/.cache`：加上 `--sandbox-writable ~/.cache/uv`，或者把缓存目录设到工作区里。
- 安装依赖需要网络：先在沙箱外装好，或者临时加上 `--sandbox-network`。
- 命令把中间文件写在 `/tmp`，下一条命令找不到：改写到工作区里。

## 验收

```bash
uv run python examples/17_sandbox.py
uv run python -m pytest tests/test_sandbox.py -q -rs
uv run python -m pytest tests -q
```

bwrap 可用时，实验输出：

```text
Write .git/config: 拒绝
Write notes.txt: 成功
工作区内写文件: 成功
写主目录: 被拦截
写 .git: 被拦截
访问网络: 被拦截
密钥环境变量: 已移除
后台进程: 已随命令结束
```

bwrap 不可用时，前两行相同，接着打印不可用的原因，实验照常结束。

[本章测试](../tests/test_sandbox.py)分两组：

- 不需要 bwrap：参数顺序、密钥变量、受保护路径、Write 和 Edit 的拒绝、工具说明；用一个假的 bwrap 脚本验证引号和超时；不可用时的报错；CLI 参数。
- 需要真实 bwrap：写入范围、`.git` 只读、断网、环境变量、额外可写目录，以及后台进程和 `setsid` 进程的清理。bwrap 不可用时这一组自动跳过，加 `-rs` 可以看到跳过原因。

另外，[阶段测试](../tests/test_stage_export.py)确认第 16 章的导出目录里还没有沙箱代码。

跳过不等于通过。在准备使用的 Linux 机器上，至少完整运行一次，确认第二组没有被跳过。

## 边界

- **读取不受限制。** 沙箱里能读 `~/.ssh`、`~/.aws` 等文件，命令输出会作为工具结果发给模型服务商。断网只能阻止命令自己把数据发出去。
- **网络只能整体开关。** 要只允许某些域名，需要在沙箱外运行代理，并让沙箱内只能连接代理。
- **受保护路径要先存在，bwrap 才能挂载。** Write、Edit 的检查不受这个限制。
- **按名称识别密钥变量。** CLI 还会移除 `--api-key-env` 指定的变量；名称里没有上述字样的其他密钥不会被识别。
- **只覆盖 Bash 和文件工具。** MCP 子进程、hooks、技能里的脚本都不在沙箱内。
- **只支持 Linux。** macOS 需要 Seatbelt（`sandbox-exec`）配置，Windows 需要另一套机制。
- **这是命名空间隔离，不是虚拟机。** 沙箱和主机共用一个内核，内核漏洞可以突破它。处理不可信代码时，仍然应该把整个 Agent 放在容器或虚拟机里运行。

作为对照：在教程固定的 Codex 提交里，`linux-sandbox`、`sandboxing`、`network-proxy`、`windows-sandbox-rs` 四个 crate 合计约 9 万行 Rust，其中网络代理和 Windows 占七成多。本章的 `sandbox.py` 约 120 行，只覆盖其中最基础的一块：Linux 上的文件系统只读和网络整体开关。

## 练习

1. 用 `--tmpfs` 隐藏 `~/.ssh` 目录，用 `--ro-bind /dev/null` 隐藏单个文件，并写一个需要真实 bwrap 的测试。
2. 把 `--mcp-command` 启动的 MCP 服务器也放进沙箱。注意它通常需要网络，并且要通过 stdin 和 stdout 与 minicode 通信。
3. 命令被拦截后，询问用户是否在沙箱外重跑。被拦截的命令可能已经执行了一半，参考第 10 章对"结果未知"的处理来设计。
4. 在 macOS 上用 `sandbox-exec` 实现一个等价的 `argv()`，让 `check()` 按平台选择实现。

## 参考

- [Codex：构造 bwrap 参数](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/linux-sandbox/src/bwrap.rs)
- [Codex：可写目录里默认只读的 `.git`、`.codex` 等路径](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/protocol/src/permissions.rs)
- [Codex：判断失败是否由沙箱造成](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/sandboxing/src/denial.rs)
- [bubblewrap 项目](https://github.com/containers/bubblewrap)

[上一章：交付](16-shipping.md) · [目录](README.md)
