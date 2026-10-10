#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Five examples using the public inc library. All core logic lives in inc/."""
import argparse
from pathlib import Path
import subprocess
import sys

from inc import DebugOptions, collect_run, debug_error, export_report, load_report, serve_report
from inc.cli import main as cli_main
from inc.versions import print_tool_versions


def example_collect(command, output_dir):
    """场景零：首次运行，实时统计并保留中断结果。"""
    return collect_run(command, output_dir)


def example_report(xml_path, project_dir, output_path):
    """场景一：XML 去重并导出带源码的离线报告。"""
    print('正在解析 XML、去重并生成 HTML 报告，请稍候……', flush=True)
    report = export_report(xml_path, output_path, project_dir=project_dir)
    print(f"报告: {Path(output_path).resolve()}，错误位置: {len(report['errors'])}")
    for error in report["errors"]:
        print(f"  {error['id']}  {error['kind']}  × {error['count']}")
    return report


def example_browse(xml_path, project_dir, port):
    """场景二：仅浏览报告，不启动目标程序。"""
    print('正在解析 XML、去重并读取源码，请稍候……', flush=True)
    report = load_report(xml_path, project_dir)
    print('正在准备网页报告并启动浏览服务……', flush=True)
    serve_report(report, port=port)


def example_debug(xml_path, project_dir, error_id, command):
    """场景三：按错误 ID 启动 Valgrind/GDB 源码断点调试。"""
    report = load_report(xml_path, project_dir)
    return debug_error(report, error_id, DebugOptions(command=command))


def example_auto_analysis(xml_path, project_dir, command, capture_dir, port):
    """场景四：网页选择错误，采集首个现场并分析越界和初始化状态。"""
    report = load_report(xml_path, project_dir)
    options = DebugOptions(command=command, auto_values=True, capture_dir=capture_dir)
    serve_report(report, options=options, port=port)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # One-command workflow uses the same library-backed CLI as aivalgrind.py.
    if argv and argv[0] in ('analyze', 'collect'):
        return cli_main(argv)
    split = argv.index("--") if "--" in argv else len(argv)
    command, argv = argv[split + 1:], argv[:split]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["collect", "report", "browse", "debug", "auto-values"])
    parser.add_argument("--xml", default="examples/sample.xml")
    parser.add_argument("--project-dir", "-p", default=".")
    parser.add_argument("--output", "-o", default="report.html")
    parser.add_argument("--error", help="debug 场景必填，使用报告中的错误 ID")
    parser.add_argument("--capture-dir", default="captures")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--output-dir', default='run-results')
    args = parser.parse_args(argv)
    if args.scenario in ("collect", "debug", "auto-values") and not command:
        parser.error("调试示例必须在 -- 后提供实际程序及参数")
    if args.scenario == "debug" and not args.error:
        parser.error("debug 示例需要 --error")
    if args.scenario in ("report", "browse") and command:
        parser.error("此示例不执行程序，无需提供 -- 后的参数")
    try:
        print_tool_versions(args.scenario)
        if args.scenario == 'collect':
            return example_collect(command, args.output_dir)
        elif args.scenario == "report":
            example_report(args.xml, args.project_dir, args.output)
        elif args.scenario == "browse":
            example_browse(args.xml, args.project_dir, args.port)
        elif args.scenario == "debug":
            return example_debug(args.xml, args.project_dir, args.error, command)
        else:
            example_auto_analysis(args.xml, args.project_dir, command, args.capture_dir, args.port)
    except KeyboardInterrupt:
        print("已中断，调试进程已执行清理。", file=sys.stderr)
        return 130
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("示例运行失败: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
