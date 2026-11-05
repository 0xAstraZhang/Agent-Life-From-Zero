# 第 17 章：亲手观察文件系统隔离

上一章：[命令行入口](16-shipping.md) · 下一章：[运行控制](18-runtime.md)

继续在 `my-agent/` 新建文件。本章先让固定命令在工作区内写入成功、在外部写入失败，再保护工作区内的 `.git`。这里不请求模型，也不使用已经封装好的沙箱类。

实际挂载实验需要 Linux 和 bubblewrap。其他平台或不支持用户命名空间的环境会明确显示跳过；不会退回普通 shell 执行实验命令。

## 1. 先写两层挂载

核心顺序只有两步：

```text
整个文件系统只读 → 把工作区重新挂成可写
```

新建 `sandbox_mounts.py`，完整写入：

```python file=sandbox_mounts.py step=minimal
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


def find_bwrap():
    if not sys.platform.startswith("linux"):
        print("SKIP：真实挂载实验只支持 Linux")
        return None
    executable = shutil.which("bwrap")
    if executable is None:
        print("SKIP：没有安装 bubblewrap")
        return None
    return str(Path(executable).resolve())


def arguments(executable, root, parent, command):
    return [
        executable,
        "--ro-bind", "/", "/",
        "--dev", "/dev", "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--ro-bind", str(parent), str(parent),
        "--bind", str(root), str(root),
        "--unshare-pid", "--die-with-parent", "--new-session", "--unshare-net",
        "--chdir", str(root), "--", "/bin/sh", "-c", command,
    ]


def launch(argv):
    return subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=5, env={"PATH": "/usr/bin:/bin"},
    )


def minimum_probe():
    executable = find_bwrap()
    if executable is None:
        return "skipped"
    with TemporaryDirectory() as directory:
        parent = Path(directory).resolve()
        root = parent / "workspace"
        root.mkdir()
        outside = parent / "outside.txt"
        outside.write_text("before", encoding="utf-8")
        check = launch(arguments(executable, root, parent, "true"))
        if check.returncode != 0:
            print("SKIP：当前内核或容器不能创建沙箱")
            return "skipped"
        command = "printf inside > inside.txt; printf changed > " + shlex.quote(str(outside))
        result = launch(arguments(executable, root, parent, command))
        inside = root / "inside.txt"
        inside_ok = inside.exists() and inside.read_text(encoding="utf-8") == "inside"
        outside_ok = outside.read_text(encoding="utf-8") == "before" and result.returncode != 0
        print("工作区写入：", inside_ok)
        print("外部文件保持原样：", outside_ok)
        if not inside_ok or not outside_ok:
            raise RuntimeError("挂载没有产生预期的写入边界")
        return "passed"
```

`arguments()` 里最后一次挂载会覆盖同一路径的早先规则。因为 `/tmp` 换成了空目录，我们先把实验的父目录只读挂回去，再只开放其中的 `workspace/`。

`launch()` 传入固定的最小环境变量。`minimum_probe()` 只运行代码中可见的固定命令，所有探针都在临时目录，不碰用户原有文件。

## 2. 运行最小实验

替换 `demo.py`：

```python file=demo.py step=minimal
from sandbox_mounts import minimum_probe

print("沙箱检查点：", minimum_probe())
```

运行 `uv run python demo.py`。Linux 上应看到两项检查为 `True`，最后为 `passed`；不支持时明确显示 `SKIP` 和 `skipped`。

安装 bubblewrap 不代表内核一定允许创建沙箱，所以执行写入探针前先运行 `true`。跳过不是通过，使用前要在目标 Linux 环境完成实验。

## 3. 增强：在可写工作区中保护一个子目录

现在增加第三次挂载：整个工作区可写，但 `.git` 再次变成只读。

新建 `protected_mounts.py`，完整写入：

```python file=protected_mounts.py step=enhanced
from pathlib import Path
from tempfile import TemporaryDirectory

from sandbox_mounts import arguments, find_bwrap, launch


def protected_arguments(executable, root, parent, command):
    argv = arguments(executable, root, parent, command)
    protected = root / ".git"
    index = argv.index("--chdir")
    argv[index:index] = ["--ro-bind", str(protected), str(protected)]
    return argv


def write_file(root, relative, content):
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("文件必须位于工作区")
    if path.is_relative_to(root / ".git"):
        raise ValueError(".git 是受保护路径")
    path.write_text(content, encoding="utf-8")
    return "已写入"


def protected_probe():
    with TemporaryDirectory() as directory:
        parent = Path(directory).resolve()
        root = parent / "workspace"
        root.mkdir()
        (root / ".git").mkdir()
        config = root / ".git" / "config"
        config.write_text("before", encoding="utf-8")
        denied = False
        try:
            write_file(root, ".git/config", "changed")
        except ValueError:
            denied = True
        print("直接写入保护：", denied)
        if not denied or config.read_text(encoding="utf-8") != "before":
            raise RuntimeError("Python 文件工具没有保护 .git")

        executable = find_bwrap()
        if executable is None:
            return "skipped"
        check = launch(protected_arguments(executable, root, parent, "true"))
        if check.returncode != 0:
            print("SKIP：当前内核或容器不能创建沙箱")
            return "skipped"
        command = "printf ok > notes.txt; printf changed > .git/config"
        result = launch(protected_arguments(executable, root, parent, command))
        notes = root / "notes.txt"
        normal_ok = notes.exists() and notes.read_text(encoding="utf-8") == "ok"
        protected_ok = result.returncode != 0 and config.read_text(encoding="utf-8") == "before"
        print("普通文件写入：", normal_ok)
        print("Bash 写入保护：", protected_ok)
        if not normal_ok or not protected_ok:
            raise RuntimeError("受保护挂载没有生效")
        return "passed"
```

`write_file()` 为什么还要检查路径？因为它运行在 Agent 的 Python 进程里，根本没有经过 bwrap。只有 Bash 的挂载保护，会留下另一条直接写文件的通道。

先解析 `..` 和符号链接，再按路径层级检查，避免把 `.gitignore` 当作 `.git` 的子路径。这份练习只保护一个已创建的目录，便于先看清同一规则怎样覆盖两个执行入口。

## 4. 运行增强实验

替换 `demo.py`：

```python file=demo.py step=enhanced
from protected_mounts import protected_probe

print("沙箱检查点：", protected_probe())
```

再次运行 `uv run python demo.py`。所有平台都能检查直接写文件的保护；Linux 上再验证真实 Bash 的挂载规则。

现在我们已经手写了只读根目录、可写工作区、受保护子目录、直接文件写入检查和受控实验。还没有把它包装成接受任意模型命令的通用沙箱。

## 5. 接入 Agent 前还需要什么

接入真实 Bash 工具前，再逐项补：固定可信 bwrap 路径、命令引用、请求期限与进程清理、更多受保护路径、环境变量策略和准确的工具说明。审批仍决定工具是否执行，沙箱决定已允许的命令能影响哪里。

本章没有限制读取，也没有实现跨平台隔离或 MCP 进程隔离。仓库的[沙箱参考实现](../minicode/sandbox.py)覆盖更多情况；它属于后续对照材料，不是本章已经手写的成果。

练习：在 Linux 上给 `.state` 增加同样的只读挂载，再把它加入 `write_file()` 的保护列表。分别证明直接写入和 shell 写入都失败。
