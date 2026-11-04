"""核对本地链接、阶段入口，并在独立进程运行每个离线章节实验。"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        # 排除代码块，防止把示例中的 Markdown 当作页面导航。
        prose = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", prose):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).is_file():
                errors.append(f"失效链接 {path.relative_to(ROOT)} -> {target}")
    examples = sorted((ROOT / "examples").glob("[0-9][0-9]_*.py"))
    if [p.name[:2] for p in examples] != [f"{i:02}" for i in range(17)]:
        errors.append("00–16 每章必须恰好有一个实验入口")
    for path in examples:
        try:
            result = subprocess.run([sys.executable, str(path)], cwd=ROOT, text=True,
                                    capture_output=True, timeout=30)
            if result.returncode:
                errors.append(f"{path.name} 失败：\n{result.stdout}\n{result.stderr}")
            else:
                print(f"PASS {path.name}")
        except subprocess.TimeoutExpired:
            errors.append(f"{path.name} 超过 30 秒")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"本地链接有效，{len(examples)} 个离线章节实验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
