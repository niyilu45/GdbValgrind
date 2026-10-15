#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single command-line entry point and library usage examples. Core logic lives in inc/."""
from pathlib import Path
import argparse
import subprocess
import sys
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / 'inc' / 'bootstrap.py'))
from inc import DebugOptions, collect_run, debug_error, export_report, load_report, serve_report
from inc.core import serve, run_debug, save_report
from inc.collect import print_summary, summary_data
from inc.workflow import analyze_run
from inc import workflow
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


def ArgParser(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    actions = ('collect', 'report', 'analyze', 'summary', 'serve', 'debug', 'refresh-captures', 'browse', 'auto-values')
    parser.add_argument('--action', choices=actions, help='要执行的功能，例如 analyze；兼容旧子命令写法')
    # Stop interpreting tool options at the target command boundary.
    boundary = next((i for i, value in enumerate(argv) if value in ('--command', '--')), len(argv))
    command = argv[boundary + 1:] if boundary < len(argv) else []
    if boundary < len(argv) and argv[boundary] == '--command' and not command:
        parser.error('--command 后必须提供目标程序及其参数')
    argv = argv[:boundary]
    action_options = [i for i, value in enumerate(argv) if value == '--action' or value.startswith('--action=')]
    if len(action_options) > 1:
        parser.error('--action 只能指定一次')
    if action_options:
        at = action_options[0]
        if argv[at] == '--action':
            if at + 1 == len(argv) or argv[at + 1].startswith('-'):
                parser.error('--action 后必须提供功能名称')
            action = argv[at + 1]
            del argv[at:at + 2]
        else:
            action = argv.pop(at).split('=', 1)[1]
        if action not in actions:
            parser.error('未知 --action：' + action)
        argv.insert(0, action)
    if argv and argv[0] in ('browse', 'auto-values'):
        auto = argv[0] == 'auto-values'
        argv[0] = 'serve'
        if auto:
            argv.append('--auto-values')
    # Preserve old reports whose copied commands used a positional XML path.
    if len(argv) > 1 and argv[0] in ('report', 'serve', 'debug', 'summary') and not argv[1].startswith('-'):
        argv.insert(1, '--xml')
    sub = parser.add_subparsers(dest="action", required=True)
    refresh = sub.add_parser('refresh-captures', help='更新已有 full-report.html 的变量关联与筛选，不重新运行程序')
    refresh.add_argument('--output-dir', type=Path, required=True, help='包含 full-report.html 和 capture-updates.js 的结果目录')
    analyze = sub.add_parser('analyze', help='一条命令生成报告并启动自动变量采集；也可复用已有 XML')
    analyze.add_argument('--xml', type=Path, help='已有 XML，可不完整；省略则首次运行采集')
    analyze.add_argument('--output-dir', type=Path, required=True, help='结果目录；可复用，只清理清单登记的旧输出')
    analyze.add_argument('--project-dir', '-p', type=Path)
    analyze.add_argument('--cwd', type=Path)
    analyze.add_argument('--stdin-file', type=Path)
    analyze.add_argument('--port', type=int, default=8765, help='首次采集实时网页端口，0 为自动选择')
    analyze.add_argument('--no-web', action='store_true', help='关闭首次采集实时网页')
    analyze.add_argument('--pause-on-error', action='store_true', help='保存现场后暂停，进入交互式 GDB；默认自动继续至程序结束')
    analyze.add_argument('--step3-only', action='store_true', help='使用 --xml 或 --base-report 跳过步骤一、二，采集变量并更新 full-report.html')
    analyze.add_argument('--base-report', type=Path, help='步骤二 HTML，默认读取 XML 同目录的 report.html')
    collect = sub.add_parser('collect', help='首次运行 Valgrind，实时统计并保留中断结果')
    collect.add_argument('--output-dir', type=Path, required=True, help='采集目录；可复用，只清理清单登记的旧输出')
    collect.add_argument('--report', action='store_true', help='检测结束后生成完整 report.html，不启动变量采集')
    collect.add_argument('--workers', type=int, choices=range(1,9), default=4)
    collect.add_argument('--cwd', type=Path)
    collect.add_argument('--stdin-file', type=Path)
    collect.add_argument('--interval', type=float, default=1.0)
    collect.add_argument('--port', type=int, default=8765, help='实时网页端口，0 为自动选择')
    collect.add_argument('--no-web', action='store_true', help='关闭实时网页')
    collect.add_argument('--project-dir', '-p', type=Path)
    for command_parser in (collect, analyze):
        command_parser.add_argument('--plain-terminal', action='store_true', help='关闭固定状态区，用于定位终端卡顿')
        command_parser.add_argument('--output-mode', choices=('pty', 'file'), default='pty', help='程序输出方式；file 绕过伪终端')
    for name, help_text in (("summary", "显示已保存或被中断 XML 的统计"), ("report", "XML 转为离线 HTML"), ("serve", "网页浏览和启动联合调试"), ("debug", "按错误 ID 启动联合调试")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--xml", type=Path, required=True, help="输入 XML 错误报告")
        p.add_argument("--project-dir", "-p", type=Path, help="工程根目录，嵌入上下各 10 行源码")
        p.add_argument('--strict-xml', action='store_true', help='要求 XML 完整闭合；默认恢复已完整写出的记录')
        if name == "report":
            p.add_argument("--output", "-o", type=Path, default=Path("report.html"))
            p.add_argument('--workers',type=int,choices=range(1,9),default=4,help='源码/blame 并发数，1–8，默认 4')
        if name == "serve":
            p.add_argument("--port", type=int, default=8765)
            p.add_argument("--open", action="store_true")
        if name in ("serve", "debug"):
            p.add_argument("--cwd", type=Path, help="被调试程序工作目录，默认当前目录")
            p.add_argument("--stdin-file", type=Path, help="将文件作为目标程序标准输入（默认 /dev/null）")
            p.add_argument("--stop-on-error", action="store_true", help="同时在新运行的每个 Valgrind 错误处停下（包括其他错误）")
            p.add_argument("--auto-values", action="store_true", help="在实际内存错误处自动提取参数/局部变量并保存现场；跳过源码断点")
            p.add_argument("--capture-dir", type=Path, default=Path("captures"), help="自动采集输出目录，默认 captures，每次调试新建会话目录")
            p.set_defaults(frame=None)
        if name == "debug":
            p.add_argument("--error", required=True, help="HTML 中的错误 ID")
            p.add_argument("--frame", help="指定 调用栈序号:帧序号，从 0 开始")
    for mode_parser in (analyze, collect, sub.choices['report'], sub.choices['serve'], sub.choices['debug']):
        mode_parser.add_argument('--mode', choices=('fast', 'full'), default='full',
                                 help='fast: 当前行变量；full: 额外采集作用域变量（默认）；无变量采集时两种模式行为相同')
    for target_parser in (collect, analyze, sub.choices['serve'], sub.choices['debug']):
        target_parser.add_argument('--command', nargs=argparse.REMAINDER,
                                  help='目标程序及其全部参数；必须放在工具参数最后，后续参数原样传递')
    args = parser.parse_args(argv)
    if command and args.action not in ('collect', 'analyze', 'serve', 'debug'):
        parser.error('该功能不执行目标程序，不接受 --command')
    args.command = command
    return args


def AiValgrind(args):
    command = args.command
    try:
        if args.action == 'refresh-captures':
            refresh_report = getattr(workflow, 'refresh_capture_report', None)
            if not callable(refresh_report):
                raise ValueError('脚本文件版本不一致：当前加载的 ' + str(workflow.__file__) +
                                 ' 缺少 refresh_capture_report。请将项目根目录脚本及整个 inc 目录更新到同一版本后重试；无需安装额外库。')
            refresh_report(args.output_dir)
            print('已更新变量查看功能，请重新打开：' + str(args.output_dir.resolve() / 'full-report.html'))
            print('已有网页不会自动换成新查看器；请刷新该文件对应的页面（HTTP 浏览可用 Ctrl+F5）。无需重新执行步骤三。')
            return 0
        runtime_info = print_tool_versions(args.action, debug_enabled=args.action == 'serve' and bool(command))
        if args.action == 'analyze':
            return analyze_run(command, args.output_dir, xml_path=args.xml, project_dir=args.project_dir,
                               cwd=args.cwd, stdin_file=args.stdin_file, live_port=None if args.no_web else args.port,
                               plain_terminal=args.plain_terminal, output_mode=args.output_mode, runtime_info=runtime_info,
                               pause_on_error=args.pause_on_error, step3_only=args.step3_only, base_report=args.base_report, mode=args.mode)
        if args.action == 'collect':
            result = collect_run(command, args.output_dir, cwd=args.cwd, stdin_file=args.stdin_file, interval=args.interval,
                               live_port=None if args.no_web else args.port, project_dir=args.project_dir,
                               plain_terminal=args.plain_terminal, output_mode=args.output_mode, runtime_info=runtime_info)
            if args.report:
                report_path = args.output_dir.resolve() / 'report.html'
                export_report(args.output_dir / 'errors.xml', report_path,
                              project_dir=args.project_dir, workers=args.workers)
                print('完整报告已生成：' + str(report_path), flush=True)
            return result
        print('正在解析 XML、去重并读取源码，请稍候……', flush=True)
        from inc.terminal import ReportProgress
        with ReportProgress('解析与准备报告') as progress:
            report = load_report(args.xml, args.project_dir, allow_partial=not args.strict_xml,
                                 workers=getattr(args,'workers',1),progress=progress)
        args.navigation_errors = report['errors']
        if not report['finished']:
            print('提示：报告不完整，仅恢复已完整写出的错误；重复次数及退出时泄漏信息可能缺失。', file=sys.stderr)
        if args.action == 'summary':
            print_summary(summary_data(report))
            return 0
        if args.action == "report":
            if command:
                raise ValueError("report 不执行程序；请使用 debug 或 serve")
            if args.output.resolve() == args.xml.resolve():
                raise ValueError("输出文件不能覆盖输入 XML")
            print('正在生成并保存 HTML 报告……', flush=True)
            with ReportProgress('保存 HTML') as progress:
                progress('序列化并写入 HTML')
                save_report(report, args.output)
            print("已生成 " + str(args.output.resolve()) + "，去重后 " + str(len(report["errors"])) + " 类错误位置，记录次数 " + str(report["occurrences"]))
        elif args.action == "serve":
            serve(report, args)
        else:
            error = next((e for e in report["errors"] if e["id"] == args.error), None)
            if not error:
                raise ValueError("找不到错误 ID；请使用当前 XML 生成的报告")
            return run_debug(error, args)
    except KeyboardInterrupt:
        print("已中断，调试进程已执行清理。", file=sys.stderr)
        return 130
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("错误: " + str(exc), file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    return AiValgrind(ArgParser(argv))


if __name__ == "__main__":
    raise SystemExit(main())
