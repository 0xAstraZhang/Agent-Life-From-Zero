"""检查链接，并按累积清单逐章导出、隔离运行示例与回归测试。"""
import argparse
from pathlib import Path
import re
import sys
import tempfile

from stages import ROOT, export_stage, load_stages, verify_stage


def check_links():
    errors = []
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        prose = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", prose):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).is_file():
                errors.append(f"失效链接 {path.relative_to(ROOT)} -> {target}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapter", type=int, choices=range(17), help="只验收指定章，仍带入此前文件")
    parser.add_argument("--export", type=Path, help="导出指定阶段到不存在或空的目录")
    args = parser.parse_args(argv)
    if args.export is not None:
        if args.chapter is None:
            parser.error("--export 需要同时指定 --chapter")
        try:
            destination = export_stage(args.chapter, args.export)
        except ValueError as error:
            parser.error(str(error))
        print(f"已导出第 {args.chapter:02} 章到 {destination}；运行步骤见该目录的 README.md")
        return 0
    errors = check_links()
    chapters = [args.chapter] if args.chapter is not None else list(range(17))
    for chapter in chapters:
        with tempfile.TemporaryDirectory(prefix=f"minicode-ch{chapter:02}-") as temporary:
            try:
                stage = export_stage(chapter, Path(temporary))
                outputs = verify_stage(chapter, stage)
                summary = outputs[-1].splitlines()[-1]
                print(f"PASS 第 {chapter:02} 章：独立示例 + 累积测试（{summary}）", flush=True)
            except Exception as error:
                errors.append(str(error))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"本地链接有效，{len(chapters)} 个阶段通过隔离验收")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
