# 第 17 章：先看一次挂载，再给 Bash 加沙箱

上一章：[交付](16-shipping.md) · 下一章：[运行控制](18-runtime.md)

本章先观察一个现象：同一条 shell 命令可以写工作区，却不能写旁边的文件。看清挂载顺序后，再把这层隔离接入 Bash，补齐受保护路径、网络、环境变量和进程清理。

本章实验只运行明确写在代码里的本地命令，不需要模型或 API Key。真实隔离实验需要 Linux 和 bubblewrap；其他平台会明确报告跳过。

```bash
uv run python scripts/check_tutorial.py --chapter 17 --export /tmp/minicode-ch17
cd /tmp/minicode-ch17
uv sync
uv run python examples/17_sandbox.py --step minimal
```

完整练习是 [lessons/ch17.py](../lessons/ch17.py)，增强实现是 [阶段 sandbox.py](../lessons/stages/17/minicode/sandbox.py)。在 Debian/Ubuntu 上可安装 `bubblewrap`，在 Fedora 上安装同名包；先用 `bwrap --version` 确认命令存在。

## 1. 最小原型：两次挂载决定哪里可写

先读最关键的参数顺序：

```text
--ro-bind / /                 整个文件系统只读
--bind 工作区 工作区          把工作区重新挂成可写
```

后面的挂载覆盖前面同一路径的规则。程序仍能读取系统里的 Python 和库，只有工作区允许写入。

最小检查点创建临时父目录，其中有 `workspace/` 和 `outside.txt`。它在沙箱里执行固定命令：

```text
写 workspace/inside.txt → 成功
写 outside.txt         → 失败，原内容仍在
```

对应的参数构造在 `mount_command()`：

```python
def mount_command(executable, root, command):
    return [executable, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--bind", str(root), str(root),
            "--unshare-pid", "--die-with-parent", "--new-session", "--unshare-net",
            "--chdir", str(root), "--", "/bin/sh", "-c", command]
```

实验把临时父目录只读挂回去，再覆盖其中的工作区，因此即使临时目录位于 `/tmp` 也能观察两种写入结果。它用最小环境变量运行固定命令，尚不接收模型生成的命令。

`Sandbox.check()` 负责确认当前 Linux 环境能创建沙箱，并返回可信 bwrap 的绝对路径。检查失败时停止实验，不退回普通 shell。先把它作为运行前提使用，后面再展开其内部检查。

## 2. 增强检查点：给相同机制补上保护

```bash
uv run python examples/17_sandbox.py --step enhanced
```

增强版先测试 Write 对 `.git/config` 的拒绝，再在 bwrap 可用时测试真实命令。应逐项看到工作区写入成功、外部写入和 `.git` 写入被拦截、网络断开、密钥环境变量移除、后台进程结束。

这是完整 `Sandbox.argv()` 的挂载顺序：

```python
argv = [self.executable, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        "--tmpfs", "/tmp"]
for path in self.writable_roots(root):
    argv += ["--bind", str(path), str(path)]
for path in self.protected_paths(root):
    if path.exists():
        argv += ["--ro-bind", str(path), str(path)]
```

现在增加第三层：工作区可写，但 `.git`、`.minicode`、会话文件、项目说明和技能目录再次设为只读。

| 新增参数 | 解决什么问题 |
| --- | --- |
| `--tmpfs /tmp` | 每条命令拥有独立临时目录 |
| `--unsetenv NAME` | 去掉密钥类环境变量 |
| `--unshare-net` | 不让命令使用主机网络 |
| `--unshare-pid --die-with-parent` | 命令结束后清理同一 PID 命名空间中的进程 |
| `--new-session` | 脱离原终端会话 |

## 3. 把沙箱接到已有 Bash 工具

现有 Bash 已经负责超时、输出和进程清理。包装器只改变启动命令：

```python
def execute(args, ctx):
    command = "exec " + shlex.join(sandbox.argv(args["command"], ctx.cwd))
    return inner(dict(args, command=command), ctx)
```

`shlex.join` 保留每个参数的边界；原命令作为一个参数传给沙箱中的 `/bin/sh -c`。`exec` 让外层 shell 替换成 bwrap。

`sandbox_tools()` 用包装后的 Bash 替换原 Bash，其他工具保留。模型仍然发送普通 Bash 调用，Agent Loop 不需要知道这些启动参数。工具说明也同步写明哪些路径可写、是否允许网络，以及临时目录和后台进程何时消失。

## 4. 让 Write、Edit 遵守相同保护

Write 和 Edit 在主进程内运行，bwrap 不会约束它们。因此也要在调用原工具前检查受保护路径：

```python
def execute(args, ctx):
    path = resolve_inside(ctx, args["path"])
    if sandbox.protects(path, ctx.cwd):
        return ToolResult("", "受保护路径不允许修改", True)
    return inner(args, ctx)
```

路径先解析 `..` 和符号链接，再按路径层级比较。额外可写目录中的会话文件也需要重新保护。挂载只能覆盖已存在的路径，而 Write/Edit 的检查不受这一限制。

## 5. 再看环境检查与清理细节

现在回到 `Sandbox.check()`：它固定 bwrap 的绝对路径，跳过工作区及额外可写目录里的同名程序，防止下一次执行被替换。随后用真实参数运行 `true`，检查内核是否允许创建用户命名空间。

在 Linux 沙箱里，后台进程即使调用 `setsid` 离开原进程组，仍属于同一 PID 命名空间，bwrap 退出时一并结束。模型看到普通工具错误和退出码，程序不根据一条错误文本猜测是否发生了沙箱拦截。

## 6. 通过 CLI 使用完整版本

```bash
uv run minicode --sandbox --allow-tool Bash -p "运行测试并解释结果"
uv run minicode --sandbox --sandbox-writable ~/.cache/uv
uv run minicode --sandbox --sandbox-network
```

`--sandbox-writable` 增加可写目录，`--sandbox-network` 整体开放网络；两者都必须与 `--sandbox` 一起使用。配置错误或沙箱不可用时，程序在请求模型前退出。

安装依赖时若需要缓存写入或网络，应明确配置；跨命令保留的临时文件放工作区，不放每次都会清空的 `/tmp`。

## 实现范围

- 读取不受限制；工具输出仍可能把读取内容交给模型。
- 网络只有整体开关，没有域名允许列表。
- 按变量名去除常见密钥，并额外去除 `--api-key-env` 指定的变量。
- 此实现覆盖 Bash 和文件工具，不覆盖 MCP 子进程。
- 只支持 Linux；它与主机共用内核。
- 工作区里的其他文件仍能被修改，之后在沙箱外运行这些文件前应检查改动。

## 验收与练习

```bash
uv run python examples/17_sandbox.py --step minimal
uv run python examples/17_sandbox.py --step enhanced
uv run python -m pytest tests/test_lesson17.py tests/test_sandbox.py -q -rs
```

真实 bwrap 不可用时，集成验证会跳过；它不是一次成功的隔离验证。使用前在目标 Linux 环境运行两个检查点。

练习：先增加一个只读保护目录；再验证通过符号链接写入也被拒绝。之后再尝试隐藏特定读取路径或隔离 MCP，分别说明新增能力改变了哪一层。

实现参考：[bubblewrap](https://github.com/containers/bubblewrap)、[Codex 挂载参数](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/linux-sandbox/src/bwrap.rs)。
