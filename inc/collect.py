# -*- coding: utf-8 -*-
"""First-pass collection with live summaries and interruption-safe raw XML."""
from contextlib import ExitStack
import json
import html
import shlex
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

from .core import report_from_root, render_html
from .commands import command_metadata
from .paged import PagedReport
from .store import ErrorStore
from .processes import ProcessSession, interrupt_scope
from .xmlstream import XMLStream
from .terminal import TerminalProgress
from .output import prepare_output, COLLECTION_FILES
from .program_output import ProgramOutput
from .watchdog import CollectionWatchdog


def summary_data(report):
    kinds = {}
    for error in report['errors']:
        entry = kinds.setdefault(error['kind'], {'locations': 0, 'occurrences': 0})
        entry['locations'] += 1
        entry['occurrences'] += error['count']
    return {'kinds': kinds, 'locations': len(report['errors']),
            'occurrences': report['occurrences'], 'counts_complete': report['counts_complete'],
            'finished': report['finished']}


def print_summary(summary):
    prefix = '' if summary['counts_complete'] else '至少 '
    print('内存错误: %d 种 / %d 个位置 / %s%d 次%s' % (
        len(summary['kinds']), summary['locations'], prefix, summary['occurrences'],
        '' if summary['counts_complete'] else '（计数尚不完整）'), flush=True)
    for kind, item in sorted(summary['kinds'].items()):
        print('  %s: %d 个位置，%s%d 次' % (kind, item['locations'], prefix, item['occurrences']), flush=True)


def collect_run(command, output_dir, *, cwd=None, stdin_file=None, interval=1.0, live_port=None, project_dir=None,
                plain_terminal=False, output_mode='pty', runtime_info=None):
    """Run Memcheck, preserving errors.xml and atomic status.json even on Ctrl+C.

    Reuses directories by removing only manifest-owned outputs. Interruption raises
    KeyboardInterrupt after cleanup and the final summary have been saved.
    """
    started_at = datetime.now().astimezone().isoformat(timespec='seconds')
    started_clock = time.monotonic()
    if sys.platform != 'linux' or not shutil.which('valgrind'):
        raise ValueError('首次采集需要 Linux 和 Valgrind；不需要 GDB')
    if isinstance(command, (str, bytes)) or not command or not command[0]:
        raise ValueError('command 必须为非空程序参数列表')
    if any(not isinstance(arg, str) or '\0' in arg for arg in command):
        raise ValueError('程序参数必须为无 NUL 字符的字符串')
    if not 0.1 <= interval <= 60:
        raise ValueError('刷新间隔须在 0.1 到 60 秒之间')
    if output_mode not in ('pty', 'file'):
        raise ValueError('output_mode 必须为 pty 或 file')
    if live_port is not None and not 0 <= live_port <= 65535:
        raise ValueError('实时报告端口必须为 0 到 65535，0 表示自动选择')
    workdir = Path(cwd or os.getcwd()).resolve()
    executable = Path(command[0])
    if not executable.is_absolute():
        executable = workdir / executable
        if not executable.is_file():
            executable = Path(shutil.which(command[0]) or executable)
    if not executable.is_file() or not workdir.is_dir():
        raise ValueError('程序或工作目录不存在')
    directory = prepare_output(output_dir, COLLECTION_FILES, [executable, stdin_file])
    xml_path = directory / 'errors.xml'
    xml_path.touch()
    (directory / 'run.json').write_text(json.dumps({
        'schema': 1, 'xml_file': xml_path.name,
        'command': [str(executable.resolve())] + list(command[1:]),
        'cwd': str(workdir), 'stdin_file': str(Path(stdin_file).resolve()) if stdin_file else None,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    store = None
    stream = XMLStream(sink=lambda node: store.accept(node))
    state, result, parse_failure = 'running', None, ''
    last_summary = None
    live = None
    display = None


    def phase(text):
        watchdog.mark(text)
        if display is not None:
            display.set_phase(text)

    def consume(reader, limit=4 * 1024 * 1024):
        nonlocal parse_failure
        consumed = 0
        while not parse_failure and (limit is None or consumed < limit):
            if display is not None:
                display.check_quit()
            chunk = reader.read(65536)
            if not chunk:
                break
            consumed += len(chunk)
            try:
                stream.feed(chunk)
                store.commit()
            except (ET.ParseError, ValueError) as exc:
                parse_failure = str(exc)
                raise ValueError('采集 XML 解析失败，原始文件已保留: ' + parse_failure) from exc

    def publish(force=False):
        nonlocal last_summary
        summary = store.summary(stream.complete)
        payload = {**summary, 'state': state, 'exit_code': result,
                   'xml': str(xml_path), 'parse_error': parse_failure}
        if force or payload != last_summary:
            status = {**payload, 'started_at': started_at,
                      'elapsed_seconds': max(0, time.monotonic() - started_clock)}
            store.commit(status)
            temp = directory / 'status.json.tmp'
            temp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, directory / 'status.json')
            last_summary = payload
        else:
            store.commit()
        if force:
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'aivalgrind.py'),
                       'report', str(xml_path), '--output', str(directory / 'full-report.html')]
            if project_dir:
                command += ['--project-dir', str(Path(project_dir).resolve())]
            stats = '\n'.join('%s: %s' % (kind, values) for kind, values in summary['kinds'].items())
            content = '<!doctype html><meta charset="utf-8"><title>AiValgrind</title><h1>AiValgrind</h1><p>\u91c7\u96c6\u6458\u8981\uff08\u4e0d\u662f\u5b8c\u6574\u62a5\u544a\uff09</p><pre>' + html.escape(stats) + '</pre><p>\u8be6\u60c5\u5df2\u4fdd\u5b58\u5728 errors.xml \u548c results.sqlite3\u3002\u8bf7\u5728 Linux / SSH \u7ec8\u7aef\u6267\u884c\u4ee5\u4e0b\u547d\u4ee4\u5bfc\u51fa\u5b8c\u6574\u79bb\u7ebf\u62a5\u544a\uff1a</p><pre>' + html.escape(shlex.join(command)) + '</pre>'
            saved = directory / 'report.html.tmp'
            saved.write_text(content, encoding='utf-8')
            os.replace(saved, directory / 'report.html')
        if display is not None:
            display.update(summary, state, live.server.origin + '/' if live else '')

    print('采集目录: ' + str(directory) + '\n采集时按 q（无需回车）或 Ctrl+C 中断；之后可对 errors.xml 生成报告或自动分析。', flush=True)
    with ExitStack() as files:
        watchdog = files.enter_context(CollectionWatchdog(directory / 'diagnostics.log'))
        store = ErrorStore(directory / 'results.sqlite3', xml_path)
        files.callback(store.close)
        store.commit()
        if live_port is not None:
            base = report_from_root(stream.root, xml_path, xml_complete=False)
            base['project'] = str(Path(project_dir).resolve()) if project_dir else ''
            base['debug_command'] = command_metadata(stream.root, xml_path, project_dir)
            live = files.enter_context(PagedReport(store.path, base, live_port))
        reader = files.enter_context(xml_path.open('rb'))
        output = files.enter_context((directory / 'program.log').open('wb'))
        diagnostics = files.enter_context((directory / 'launcher.log').open('wb'))
        target_input = files.enter_context(open(stdin_file, 'rb')) if stdin_file else subprocess.DEVNULL
        display = files.enter_context(TerminalProgress(directory, started_at, started_clock, enabled=not plain_terminal, runtime_info=runtime_info))
        display.update(store.summary(), state, live.server.origin + '/' if live else '')
        display.start_updates()
        transport = None
        try:
            if os.name == 'posix' and output_mode == 'pty':
                transport = files.enter_context(ProgramOutput(output, diagnostics))
            with ProcessSession() as processes:
                phase('正在启动 Valgrind')
                process = processes.launch([shutil.which('valgrind'), '--tool=memcheck', '--xml=yes',
                                            '--xml-file=' + str(xml_path), '--log-file=' + str(directory / 'valgrind.log'),
                                            '--leak-check=full', '--show-leak-kinds=all', '--track-origins=yes',
                                            '--error-limit=no', str(executable.resolve())] + list(command[1:]),
                                           cwd=workdir, stdin=target_input,
                                           stdout=transport.stdout if transport else output,
                                           stderr=transport.stderr if transport else diagnostics)
                publish(True)
                while True:
                    watchdog.mark('检查键盘及后台线程')
                    display.check_quit()
                    if display.refresh_error is not None:
                        raise OSError('终端刷新线程失败（已停止采集）: ' + str(display.refresh_error)
                                      + '；可使用 --plain-terminal 关闭状态区定位问题')
                    if transport is not None and transport.error:
                        raise OSError('程序输出读取失败: ' + str(transport.error))
                    phase('正在解析新增 XML 并增量入库')
                    consume(reader)
                    watchdog.mark('去重、保存统计及生成网页')
                    publish()
                    result = process.poll()
                    if result is not None:
                        break
                    remaining = interval
                    phase('程序运行中，等待新增错误')
                    while remaining > 0:
                        watchdog.mark('等待下一次采集')
                        step = min(0.5, remaining)
                        display.wait(step)
                        remaining -= step
            state = 'finished' if result == 0 else 'failed'
        except KeyboardInterrupt:
            state = 'interrupted'
            raise
        except BaseException:
            state = 'failed'
            raise
        finally:
            # ProcessSession has already killed/reaped the producers. Drain all
            # bytes they flushed during shutdown and leave an atomic checkpoint.
            try:
                with interrupt_scope(signal.SIG_IGN):
                    if transport is not None:
                        transport.close()
                with interrupt_scope():
                    phase('正在解析剩余 XML，保存最终结果（Ctrl+C 可跳过）')
                    consume(reader, limit=None)
                    if stream.started and not parse_failure:
                        stream.finish()
                    publish(True)
            except KeyboardInterrupt:
                state = 'interrupted'
                raise
            finally:
                with interrupt_scope(signal.SIG_IGN):
                    display.__exit__(None, None, None)
                print('开始时间: %s | 已运行: %.1f 秒 | 状态: %s' % (started_at, max(0, time.monotonic() - started_clock), state), flush=True)
                if last_summary:
                    print_summary(last_summary)
                print('原始结果已保留: ' + str(xml_path) + '（若跳过最终保存，可稍后从 XML 重新生成报告）', flush=True)
    return result
