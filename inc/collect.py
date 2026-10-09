# -*- coding: utf-8 -*-
"""First-pass collection with live summaries and interruption-safe raw XML."""
from contextlib import ExitStack
import json
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
from .live import LiveReport
from .processes import ProcessSession, interrupt_scope
from .xmlstream import XMLStream
from .terminal import TerminalProgress


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


def collect_run(command, output_dir, *, cwd=None, stdin_file=None, interval=1.0, live_port=None, project_dir=None):
    """Run Memcheck, preserving errors.xml and atomic status.json even on Ctrl+C.

    output_dir must not exist. Returns the target exit code; interruption raises
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
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    xml_path = directory / 'errors.xml'
    xml_path.touch()
    (directory / 'run.json').write_text(json.dumps({
        'schema': 1, 'xml_file': xml_path.name,
        'command': [str(executable.resolve())] + list(command[1:]),
        'cwd': str(workdir), 'stdin_file': str(Path(stdin_file).resolve()) if stdin_file else None,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    stream = XMLStream()
    state, result, parse_failure = 'running', None, ''
    last_summary = None
    live = None
    display = None

    def consume(reader, limit=4 * 1024 * 1024):
        nonlocal parse_failure
        consumed = 0
        while not parse_failure and (limit is None or consumed < limit):
            chunk = reader.read(65536)
            if not chunk:
                break
            consumed += len(chunk)
            try:
                stream.feed(chunk)
            except (ET.ParseError, ValueError) as exc:
                parse_failure = str(exc)
                raise ValueError('采集 XML 解析失败，原始文件已保留: ' + parse_failure) from exc

    def publish(force=False):
        nonlocal last_summary
        report = report_from_root(stream.root, xml_path, xml_complete=stream.complete)
        summary = summary_data(report)
        payload = {**summary, 'state': state, 'exit_code': result,
                   'xml': str(xml_path), 'parse_error': parse_failure}
        if force or payload != last_summary:
            temp = directory / 'status.json.tmp'
            with temp.open('w', encoding='utf-8') as file:
                json.dump({**payload, 'started_at': started_at, 'elapsed_seconds': max(0, time.monotonic() - started_clock)}, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, directory / 'status.json')
            last_summary = payload
            if live is not None:
                detailed = report_from_root(stream.root, xml_path, project_dir, xml_complete=stream.complete)
                detailed['debug_command'] = command_metadata(stream.root, xml_path, project_dir)
                live.update(detailed, state)
                saved = directory / 'report.html.tmp'
                saved.write_text(render_html(detailed), encoding='utf-8')
                os.replace(saved, directory / 'report.html')
        if display is not None:
            display.render(summary, state, live.server.origin + '/' if live else '')

    print('采集目录: ' + str(directory) + '\nCtrl+C 可中断；之后可对 errors.xml 生成报告或自动分析。', flush=True)
    with ExitStack() as files:
        if live_port is not None:
            live = files.enter_context(LiveReport(report_from_root(stream.root, xml_path), live_port))
        reader = files.enter_context(xml_path.open('rb'))
        output = files.enter_context((directory / 'program.log').open('wb'))
        diagnostics = files.enter_context((directory / 'launcher.log').open('wb'))
        target_input = files.enter_context(open(stdin_file, 'rb')) if stdin_file else subprocess.DEVNULL
        display = files.enter_context(TerminalProgress(directory, started_at, started_clock))
        try:
            with ProcessSession() as processes:
                process = processes.launch([shutil.which('valgrind'), '--tool=memcheck', '--xml=yes',
                                            '--xml-file=' + str(xml_path), '--log-file=' + str(directory / 'valgrind.log'),
                                            '--leak-check=full', '--show-leak-kinds=all', '--track-origins=yes',
                                            '--error-limit=no', str(executable.resolve())] + list(command[1:]),
                                           cwd=workdir, stdin=target_input, stdout=output, stderr=diagnostics)
                publish(True)
                while True:
                    consume(reader)
                    publish()
                    result = process.poll()
                    if result is not None:
                        break
                    time.sleep(interval)
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
            with interrupt_scope(signal.SIG_IGN):
                try:
                    consume(reader, limit=None)
                    if stream.started and not parse_failure:
                        stream.finish()
                finally:
                    try:
                        publish(True)
                    finally:
                        display.__exit__(None, None, None)
                    print('开始时间: %s | 已运行: %.1f 秒 | 状态: %s' % (started_at, max(0, time.monotonic() - started_clock), state), flush=True)
                    if last_summary:
                        print_summary(last_summary)
                    print('结果已保留: ' + str(xml_path), flush=True)
    return result
