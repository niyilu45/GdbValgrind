# -*- coding: utf-8 -*-
"""Compatible command-line interface; library users should import inc."""
import argparse
from pathlib import Path
import subprocess
import sys
from .core import load_report, render_html, serve, run_debug, save_report
from .collect import collect_run, print_summary, summary_data
from .workflow import analyze_run
from . import workflow
from .versions import print_tool_versions

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Split explicitly: argparse REMAINDER otherwise swallows report options.
    command = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__)
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
    analyze.add_argument('--step3-only', action='store_true', help='使用 --xml 跳过步骤一、二，直接采集变量并生成 full-report.html')
    analyze.add_argument('--base-report', type=Path, help='步骤二 HTML，默认读取 XML 同目录的 report.html')
    collect = sub.add_parser('collect', help='首次运行 Valgrind，实时统计并保留中断结果')
    collect.add_argument('--output-dir', type=Path, required=True, help='采集目录；可复用，只清理清单登记的旧输出')
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
        p.add_argument("xml", type=Path)
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
    args = parser.parse_args(argv)
    args.command = command
    try:
        if args.action == 'refresh-captures':
            refresh_report = getattr(workflow, 'refresh_capture_report', None)
            if not callable(refresh_report):
                raise ValueError('脚本文件版本不一致：当前加载的 ' + str(workflow.__file__) +
                                 ' 缺少 refresh_capture_report。请将项目根目录脚本及整个 inc 目录更新到同一版本后重试；无需安装额外库。')
            refresh_report(args.output_dir)
            print('已更新变量查看功能，请重新打开：' + str(args.output_dir.resolve() / 'full-report.html'))
            return 0
        runtime_info = print_tool_versions(args.action, debug_enabled=args.action == 'serve' and bool(command))
        if args.action == 'analyze':
            return analyze_run(command, args.output_dir, xml_path=args.xml, project_dir=args.project_dir,
                               cwd=args.cwd, stdin_file=args.stdin_file, live_port=None if args.no_web else args.port,
                               plain_terminal=args.plain_terminal, output_mode=args.output_mode, runtime_info=runtime_info,
                               pause_on_error=args.pause_on_error, step3_only=args.step3_only, base_report=args.base_report)
        if args.action == 'collect':
            return collect_run(command, args.output_dir, cwd=args.cwd, stdin_file=args.stdin_file, interval=args.interval,
                               live_port=None if args.no_web else args.port, project_dir=args.project_dir,
                               plain_terminal=args.plain_terminal, output_mode=args.output_mode, runtime_info=runtime_info)
        print('正在解析 XML、去重并读取源码，请稍候……', flush=True)
        from .terminal import ReportProgress
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
