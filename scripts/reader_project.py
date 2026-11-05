"""从教程正文的完整文件代码块构建独立读者项目，并隔离验证每个检查点。"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from functools import lru_cache
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"lessons", "minicode", "examples"}
FENCES = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.M | re.S)


@dataclass
class ReaderStep:
    chapter: int
    name: str
    files: dict[str, str]
    source: Path


def relative_file(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute() or
            any(part in {"", ".", "..", ".git", ".venv"} for part in value.split("/"))):
        raise ValueError(f"正文文件路径必须是项目内的相对路径：{value}")
    return str(path)


def reader_steps(root: Path = ROOT) -> list[ReaderStep]:
    """按章和 step 首次出现顺序，读取标为完整文件的正文块。"""
    root = Path(root)
    result = []
    for source in sorted((root / "docs").glob("[0-9][0-9]-*.md")):
        chapter = int(source.name[:2])
        by_name = {}
        for match in FENCES.finditer(source.read_text(encoding="utf-8")):
            header, body = match.groups()
            if "file=" in header and "\\" in header:
                raise ValueError(f"正文文件路径必须是项目内的相对路径：{header}")
            words = shlex.split(header)
            attributes = dict(word.split("=", 1) for word in words[1:] if "=" in word)
            if not ({"file", "step"} & attributes.keys()):
                continue
            if not {"file", "step"} <= attributes.keys():
                raise ValueError(f"{source.name} 的完整文件块需要同时标注 file 和 step")
            if not words or words[0] not in {"python", "text", "toml", "json"}:
                raise ValueError(f"{source.name} 的完整文件块语言不受支持：{header}")
            name = attributes["step"]
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
                raise ValueError(f"无效检查点名称：{name}")
            if name not in by_name:
                point = ReaderStep(chapter, name, {}, source)
                by_name[name] = point
                result.append(point)
            path = relative_file(attributes["file"])
            by_name[name].files[path] = body if body.endswith("\n") else body + "\n"
    return result


def reader_files(chapter: int, step: str | None = None, root: Path = ROOT) -> dict[str, str]:
    points = reader_steps(root)
    target = [point for point in points if point.chapter == chapter]
    if not target:
        raise ValueError(f"第 {chapter:02} 章没有正文完整文件检查点")
    if step is None:
        step = target[-1].name
    if step not in {point.name for point in target}:
        raise ValueError(f"第 {chapter:02} 章没有 step={step}")
    files = {}
    for point in points:
        if point.chapter > chapter:
            break
        files.update(point.files)
        if point.chapter == chapter and point.name == step:
            return files
    raise ValueError("没有找到指定检查点")


def validate_sources(files: dict[str, str]):
    for name, source in files.items():
        relative_file(name)
        if not name.endswith(".py"):
            continue
        try:
            tree = ast.parse(source, filename=name)
            compile(tree, name, "exec")
        except SyntaxError as error:
            raise ValueError(f"正文文件 {name} 无法编译：{error}") from error
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            elif isinstance(node, ast.Call):
                function = node.func
                dynamic = isinstance(function, ast.Name) and function.id == "__import__"
                dynamic |= isinstance(function, ast.Attribute) and function.attr == "import_module"
                if dynamic and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    imported = [node.args[0].value]
            for module in imported:
                if module.split(".")[0] in FORBIDDEN:
                    raise ValueError(f"正文文件 {name} 不能借用仓库模块 {module}")


def normalized_distribution(name):
    return re.sub(r"[-_.]+", "-", name).lower()


@lru_cache(maxsize=1)
def installed_distributions():
    return importlib.metadata.packages_distributions()


def validate_dependencies(files: dict[str, str], distribution_map=None):
    """不能因为维护环境碰巧安装了库，就忽略读者项目漏写的依赖。"""
    if "pyproject.toml" not in files:
        raise ValueError("正文项目缺少 pyproject.toml，无法核对依赖声明")
    try:
        project = tomllib.loads(files["pyproject.toml"]).get("project", {})
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"正文 pyproject.toml 不是合法 TOML：{error}") from error
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
        raise ValueError("project.dependencies 必须是依赖字符串列表")
    declared = set()
    for dependency in dependencies:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", dependency)
        if not match:
            raise ValueError(f"无法解析依赖声明：{dependency}")
        declared.add(normalized_distribution(match.group(1)))
    local_modules = {PurePosixPath(name).parts[0].removesuffix(".py")
                     for name in files if name.endswith(".py")}
    distributions = installed_distributions() if distribution_map is None else distribution_map
    for name, source in files.items():
        if not name.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(source, filename=name)):
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                modules = [(node.module or "").split(".")[0]]
            else:
                continue
            for module in modules:
                if module in sys.stdlib_module_names or module in local_modules:
                    continue
                providers = distributions.get(module, [])
                if not providers:
                    raise ValueError(f"正文文件 {name} 导入第三方模块 {module}，但维护环境没有对应的分发元数据；"
                                     "请先声明并安装该依赖")
                if not any(normalized_distribution(provider) in declared for provider in providers):
                    raise ValueError(f"正文文件 {name} 导入 {module}，但 pyproject.toml 未声明依赖："
                                     + " / ".join(providers))


def export_reader(chapter: int, destination: Path | str, step: str | None = None,
                  root: Path = ROOT) -> Path:
    destination = Path(destination).expanduser().resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("读者项目导出目录必须不存在或为空，不会覆盖已有文件")
    files = reader_files(chapter, step, root)
    validate_sources(files)
    if "demo.py" not in files or "pyproject.toml" not in files:
        raise ValueError("正文项目需要提供 demo.py 和 pyproject.toml 的完整文件块")
    validate_dependencies(files)
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    actual_step = step or [point.name for point in reader_steps(root) if point.chapter == chapter][-1]
    if "README.md" not in files:
        (destination / "README.md").write_text(f'''# 第 {chapter:02} 章：{actual_step}

这些文件逐份来自教程正文，累计了此前章节以及本章到此检查点的代码。
本目录不依赖教程仓库的 lessons、minicode 或 examples。

在当前目录安装依赖，然后运行：

```bash
uv sync
uv run python demo.py
```

也可以激活自己的 Python 环境后运行 `python demo.py`。
模型实验沿用同一组 MINICODE_MODEL、MINICODE_BASE_URL、MINICODE_API_KEY 环境变量；
密钥不写进源码。默认连接真实 API，不需要先实现假模型。
下一检查点如果给出同名完整文件，替换该文件后继续运行 demo.py。
''', encoding="utf-8")
    return destination


def load_cases(root: Path = ROOT) -> dict:
    """维护验证夹具独立存放在 tests；学习项目不会导入它们。"""
    root = Path(root)
    cases = {}
    test_path = str(root / "tests")
    sys.path.insert(0, test_path)
    try:
        for path in sorted((root / "tests").glob("reader_cases_*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for key, value in module.CASES.items():
                if key in cases:
                    raise ValueError(f"重复读者验证夹具：{key}")
                cases[key] = value
    finally:
        sys.path.remove(test_path)
    return cases


NETWORK_GUARD = '''import importlib.abc
import os
from pathlib import Path
import socket
import sys

checkout = Path(os.environ["READER_CHECKOUT"]).resolve()
sys.path[:] = [value for value in sys.path if value and
               (not Path(value).resolve().is_relative_to(checkout) or "site-packages" in Path(value).parts)]

class ReaderOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"lessons", "minicode", "examples"}:
            raise ModuleNotFoundError("reader project cannot import repository module " + fullname, name=fullname)

sys.meta_path.insert(0, ReaderOnly())

def _reader_no_network(*args, **kwargs):
    raise RuntimeError("reader validation forbids real network access")

socket.socket.connect = _reader_no_network
socket.socket.connect_ex = _reader_no_network
socket.create_connection = _reader_no_network
'''

BOOTSTRAP = r'''
import importlib.abc
import json
import os
from pathlib import Path
import runpy
import sys

stage, checkout, fixture_path, report_path = map(Path, sys.argv[1:5])
# Keep installed dependencies, but remove editable checkout paths.
def allowed(value):
    if not value:
        return False
    path = Path(value).resolve()
    return not path.is_relative_to(checkout) or "site-packages" in path.parts
sys.path[:] = [str(stage)] + [value for value in sys.path if allowed(value)]
class ReaderOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"lessons", "minicode", "examples"}:
            raise ModuleNotFoundError("reader project cannot import repository module " + fullname, name=fullname)
sys.meta_path.insert(0, ReaderOnly())
for name in list(sys.modules):
    if name.split(".")[0] in {"lessons", "minicode", "examples"}:
        del sys.modules[name]
os.chdir(stage)
fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
responses = fixture["responses"]
requests, used, unexpected = [], 0, 0
import socket

def no_network(*args, **kwargs):
    raise RuntimeError("reader validation forbids real network access")
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
import httpx

def serve(request):
    global used, unexpected
    headers = {key: ("<redacted>" if any(word in key.lower() for word in ("authorization", "api-key", "token")) else value)
               for key, value in request.headers.items()}
    try:
        body = json.loads(request.content)
    except (ValueError, UnicodeDecodeError):
        body = request.content.decode("utf-8", errors="replace")
    requests.append({"method": request.method, "url": str(request.url), "headers": headers, "json": body})
    if used >= len(responses):
        unexpected += 1
        raise AssertionError("reader made an unexpected extra HTTP request")
    response = responses[used]
    used += 1
    if isinstance(response, dict) and "raise" in response:
        error = getattr(httpx, response["raise"])
        raise error(response.get("message", "fixture error"), request=request)
    if isinstance(response, dict) and "sse" in response:
        return httpx.Response(response.get("status_code", 200),
            headers={"content-type": "text/event-stream", **response.get("headers", {})}, content=response["sse"].encode())
    if isinstance(response, dict) and "status_code" in response and "json" in response:
        return httpx.Response(response["status_code"], headers=response.get("headers", {}), json=response["json"])
    return httpx.Response(200, json=response)

original_init = httpx.Client.__init__
def fixture_init(self, *args, **kwargs):
    kwargs["transport"] = httpx.MockTransport(serve)
    kwargs["mounts"] = {}
    kwargs["proxy"] = None
    kwargs["trust_env"] = False
    original_init(self, *args, **kwargs)
httpx.Client.__init__ = fixture_init
original_async_init = httpx.AsyncClient.__init__
def fixture_async_init(self, *args, **kwargs):
    kwargs["transport"] = httpx.MockTransport(serve)
    kwargs["mounts"] = {}
    kwargs["proxy"] = None
    kwargs["trust_env"] = False
    original_async_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = fixture_async_init
try:
    sys.argv = [str(stage / "demo.py"), *fixture.get("args", [])]
    runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    report_path.write_text(json.dumps({"requests": requests, "responses_used": used,
                                      "unexpected_requests": unexpected}, ensure_ascii=False), encoding="utf-8")
'''


def verify_reader(chapter: int, step: str, destination: Path | str, case: dict,
                  timeout: int = 60, root: Path = ROOT) -> dict:
    destination = Path(destination).resolve()
    files = {str(path.relative_to(destination)): path.read_text(encoding="utf-8")
             for path in destination.rglob("*.py")}
    validate_sources(files)
    config = destination / "pyproject.toml"
    if config.is_file():
        files["pyproject.toml"] = config.read_text(encoding="utf-8")
    validate_dependencies(files)
    responses = case.get("responses", [])
    with tempfile.TemporaryDirectory(prefix="reader-harness-") as temporary:
        harness = Path(temporary)
        fixture = harness / "fixture.json"
        report = harness / "report.json"
        fixture.write_text(json.dumps({"responses": responses, "args": case.get("args", [])}, ensure_ascii=False), encoding="utf-8")
        # Child Python programs (for example the MCP echo server) also inherit a network guard.
        (harness / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if not any(word in key.upper() for word in ("TOKEN", "API_KEY", "SECRET", "CREDENTIAL"))
               and not key.startswith(("MINICODE_", "LLM_", "OPENAI_", "ANTHROPIC_"))}
        for prefix in ("MINICODE", "LLM"):
            env.update({f"{prefix}_MODEL": "reader-model", f"{prefix}_BASE_URL": "https://reader.invalid/v1",
                        f"{prefix}_API_KEY": "reader-test-key", f"{prefix}_API": "openai-chat"})
        env["PYTHONPATH"] = str(harness)
        env["READER_CHECKOUT"] = str(Path(root).resolve())
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        result = subprocess.run([sys.executable, "-I", "-c", BOOTSTRAP, str(destination),
                                 str(Path(root).resolve()), str(fixture), str(report)],
                                cwd=destination, env=env, input="".join(str(line) + "\n" for line in case.get("inputs", [])),
                                text=True, capture_output=True, timeout=timeout)
        output = result.stdout + result.stderr
        label = f"正文第 {chapter:02} 章 {step}"
        if result.returncode != case.get("expected_exit", 0):
            raise RuntimeError(f"{label} 退出码 {result.returncode}：\n{output}")
        if not report.exists():
            raise RuntimeError(f"{label} 没有完成隔离验证记录：\n{output}")
        trace = json.loads(report.read_text(encoding="utf-8"))
        if trace["responses_used"] != len(responses) or trace["unexpected_requests"]:
            raise AssertionError(f"{label} HTTP 请求不符：提供 {len(responses)} 个响应，使用 {trace['responses_used']} 个，"
                                 f"额外请求 {trace['unexpected_requests']} 次\n{output}")
        for expected in case.get("expected_output", []):
            if expected not in output:
                raise AssertionError(f"{label} 缺少输出 {expected!r}：\n{output}")
        for name, expected in case.get("expected_files", {}).items():
            target = destination / relative_file(name)
            if expected is None:
                if target.exists():
                    raise AssertionError(f"{label} 不应产生文件 {name}")
            elif not target.is_file() or target.read_text(encoding="utf-8") != expected:
                raise AssertionError(f"{label} 文件 {name} 不符合预期")
        checker = case.get("check")
        if checker:
            checker(trace["requests"], destination, output)
        return {"chapter": chapter, "step": step, "output": output, "requests": trace["requests"],
                "responses_used": trace["responses_used"], "returncode": result.returncode}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--step")
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        destination = export_reader(args.chapter, args.export, args.step)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"正文项目已导出到 {destination}；在该目录运行 uv sync 和 uv run python demo.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
