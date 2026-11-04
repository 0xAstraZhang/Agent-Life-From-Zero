"""累积文件清单、无覆盖导出，以及不允许回落到最终包的隔离执行。"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "lessons/stages.json"


def load_stages():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("不支持的阶段清单版本")
    stages = data["stages"]
    if [stage["chapter"] for stage in stages] != list(range(17)):
        raise ValueError("阶段清单必须按顺序包含 00–16")
    return stages


def stage_files(chapter: int) -> dict[str, str]:
    if chapter not in range(17):
        raise ValueError("chapter 必须在 0–16 之间")
    files = {}
    for stage in load_stages()[:chapter + 1]:
        files.update(stage["files"])
    return files


def _project(chapter, files):
    if chapter == 16:
        return (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    packages = [name for name in ("minicode", "lessons") if name + "/__init__.py" in files]
    text = '''[project]
name = "minicode-lesson"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["httpx>=0.28,<1", "jsonschema>=4.23,<5"]

[dependency-groups]
dev = ["pytest>=8,<10"]

[tool.pytest.ini_options]
testpaths = ["tests"]
'''
    if packages:
        text += '''
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ''' + json.dumps(packages) + "\n"
    return text


def export_stage(chapter: int, destination: str | Path) -> Path:
    destination = Path(destination).expanduser().resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("导出目录必须不存在或为空；不会覆盖已有练习文件")
    files = stage_files(chapter)
    sources = {}
    for relative, source in files.items():
        target = (destination / relative).resolve()
        origin = (ROOT / source).resolve()
        if not target.is_relative_to(destination) or not origin.is_relative_to(ROOT):
            raise ValueError("清单路径不能越界")
        if not origin.is_file():
            raise ValueError(f"阶段源码不存在：{source}")
        sources[relative] = origin
    destination.mkdir(parents=True, exist_ok=True)
    for relative, source in sources.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (destination / "pyproject.toml").write_text(_project(chapter, files), encoding="utf-8")
    stage = load_stages()[chapter]
    prior = stage_files(chapter - 1) if chapter else {}
    changes = "\n".join(f"- {'替换' if target in prior else '新增'} `{target}`（来源：`{source}`）"
                        for target, source in stage["files"].items())
    readme = f'''# 第 {chapter:02} 章：{stage['title']}

这是截至本章的完整累积目录。它只包含本章及之前的代码，不依赖原仓库的最终 minicode 包。
可以直接修改这里的文件，也可以对照它检查自己的手写实现。导出器不会覆盖已有内容。

## 运行

在这个导出目录内执行：

```bash
uv sync
uv run python {stage['example']}
uv run python -m pytest tests -q
```

这里有独立的 pyproject.toml，首次 uv sync 会生成此阶段自己的锁文件。示例与测试默认不请求真实模型，也不需要密钥。
所有此前章节的测试仍然保留，当前目录里的 tests 是截至本章的回归集。

## 本章新增或替换

{changes}

## 下一章

保留当前练习目录。阅读下一章时，按它的清单增加或替换文件；也可以导出下一阶段到另一个空目录进行比较。
'''
    if chapter >= 3:
        readme += '''
## 可选的真实接口验证

环境中已有 LLM_MODEL / LLM_BASE_URL / LLM_API_KEY 时，可以运行：

```bash
uv run python examples/live_model.py --api openai-chat --env-prefix LLM
```

只选择本阶段已经实现的协议。第 04 章增加 openai-responses，第 05 章增加 anthropic-messages；
入口会根据已知的 DeepSeek 地址选择其对应协议路径；其他服务按该服务文档显式指定 --base-url 与 --provider。
第 08 章以后可以再加 --stream。真实请求会产生服务商费用；脚本不会输出密钥。
'''
    (destination / "README.md").write_text(readme, encoding="utf-8")
    (destination / ".gitignore").write_text(".venv/\n__pycache__/\n.pytest_cache/\n.env*\n.minicode/\n", encoding="utf-8")
    return destination


# Executed in a fresh -I process. PathFinder bypasses any editable-install finder.
# A missing local module raises immediately rather than falling through to the checkout.
BOOTSTRAP = r'''
import importlib.abc
import importlib.machinery
import os
from pathlib import Path
import runpy
import sys

stage = Path(sys.argv[1]).resolve()
checkout = Path(sys.argv[2]).resolve()
mode = sys.argv[3]
args = sys.argv[4:]
def allowed_path(value):
    if not value:
        return False
    candidate = Path(value).resolve()
    return not candidate.is_relative_to(checkout) or "site-packages" in candidate.parts
sys.path[:] = [str(stage)] + [value for value in sys.path if allowed_path(value)]
class StageOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] not in {"minicode", "lessons", "examples", "tests"}:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, [str(stage)] if path is None else path)
        locations = [] if spec is None else list(spec.submodule_search_locations or [])
        if spec is not None and spec.origin not in {None, "namespace"}:
            locations.append(spec.origin)
        if spec is None or any(not Path(location).resolve().is_relative_to(stage) for location in locations):
            raise ModuleNotFoundError(f"阶段中没有模块 {fullname}，禁止回落到最终仓库", name=fullname)
        return spec
sys.meta_path.insert(0, StageOnly())
for name in list(sys.modules):
    if name.split(".")[0] in {"minicode", "lessons", "examples", "tests"}:
        del sys.modules[name]
os.chdir(stage)
# Prevent optional third-party plugins from importing application modules before the test.
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
if mode == "example":
    sys.argv = [str(stage / args[0]), *args[1:]]
    runpy.run_path(sys.argv[0], run_name="__main__")
elif mode == "pytest":
    import pytest
    raise SystemExit(pytest.main(["-q", "--maxfail=1", "-p", "no:cacheprovider", *args]))
elif mode == "probe":
    exec(args[0], {"__name__": "__main__"})
else:
    raise ValueError(mode)
'''


def run_isolated(stage: Path, mode: str, *args: str, timeout: int = 60):
    return subprocess.run([sys.executable, "-I", "-c", BOOTSTRAP, str(stage), str(ROOT), mode, *args],
                          cwd=stage, text=True, capture_output=True, timeout=timeout)


def verify_stage(chapter: int, destination: Path):
    example = load_stages()[chapter]["example"]
    outputs = []
    for mode, target in (("example", example), ("pytest", "tests")):
        result = run_isolated(destination, mode, target)
        if result.returncode:
            raise RuntimeError(f"第 {chapter:02} 章 {mode} 失败：\n{result.stdout}\n{result.stderr}")
        outputs.append(result.stdout.strip())
    return outputs
