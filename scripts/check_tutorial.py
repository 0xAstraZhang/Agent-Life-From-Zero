"""检查正文独立项目，并保留参考实现的阶段检查点与回归测试。"""
import argparse
from pathlib import Path
import re
import sys
import tempfile

if __package__:
    from .stages import ROOT, checkpoints, export_stage, load_stages, verify_stage
    from .reader_project import export_reader, load_cases, reader_steps, verify_reader
else:
    from stages import ROOT, checkpoints, export_stage, load_stages, verify_stage
    from reader_project import export_reader, load_cases, reader_steps, verify_reader


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
    chapters_all = range(len(load_stages()))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapter", type=int, choices=chapters_all, help="只验收指定章，仍带入此前文件")
    parser.add_argument("--export", type=Path, help="导出指定阶段到不存在或空的目录")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reader-only", action="store_true", help="只检查正文构建的独立项目")
    mode.add_argument("--reference-only", action="store_true", help="只检查参考实现的阶段文件")
    args = parser.parse_args(argv)
    if args.export is not None:
        if args.reader_only:
            parser.error("正文项目请用 scripts/reader_project.py --chapter N --step NAME --export DIR 导出")
        if args.chapter is None:
            parser.error("--export 需要同时指定 --chapter")
        try:
            destination = export_stage(args.chapter, args.export)
        except ValueError as error:
            parser.error(str(error))
        print(f"已导出第 {args.chapter:02} 章到 {destination}；运行步骤见该目录的 README.md")
        return 0
    errors = check_links()
    chapters = [args.chapter] if args.chapter is not None else list(chapters_all)
    reader_count = 0
    if not args.reference_only:
        try:
            points = [point for point in reader_steps() if point.chapter in chapters]
            cases = load_cases()
            for chapter in chapters:
                if not any(point.chapter == chapter for point in points):
                    errors.append(f"第 {chapter:02} 章缺少正文完整文件检查点")
            for point in points:
                key = (point.chapter, point.name)
                if key not in cases:
                    errors.append(f"正文检查点缺少验证夹具：{key}")
                    continue
                with tempfile.TemporaryDirectory(prefix=f"reader-ch{point.chapter:02}-") as temporary:
                    try:
                        project = export_reader(point.chapter, temporary, point.name)
                        result = verify_reader(point.chapter, point.name, project, cases[key])
                        reader_count += 1
                        print(f"PASS 正文第 {point.chapter:02} 章 {point.name}：空目录构建 + {len(result['requests'])} 次 HTTP 往返", flush=True)
                    except Exception as error:
                        errors.append(str(error))
        except Exception as error:
            errors.append(str(error))
    if not args.reader_only:
        for chapter in chapters:
            with tempfile.TemporaryDirectory(prefix=f"minicode-ch{chapter:02}-") as temporary:
                try:
                    stage = export_stage(chapter, Path(temporary))
                    outputs = verify_stage(chapter, stage)
                    summary = outputs[-1].splitlines()[-1]
                    print(f"PASS 参考第 {chapter:02} 章：{len(checkpoints(chapter))} 个检查点 + 累积测试（{summary}）", flush=True)
                except Exception as error:
                    errors.append(str(error))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"本地链接有效，{reader_count} 个正文检查点通过；参考阶段检查{'已跳过' if args.reader_only else '通过'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
