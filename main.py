#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single command-line entry point and library usage examples. Core logic lives in inc/."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / 'inc' / 'bootstrap.py'))
from inc import DebugOptions, collect_run, debug_error, export_report, load_report, serve_report
from inc.cli import ArgParser, AiValgrind


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
    return AiValgrind(ArgParser(argv))


if __name__ == "__main__":
    raise SystemExit(main())
