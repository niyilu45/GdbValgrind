"""One-command or saved-report entry to interactive automatic value capture."""
from pathlib import Path
import shutil
import subprocess
import sys

from . import core
from .api import DebugOptions, debug_error, export_report
from .collect import collect_run
from .processes import ProcessSession
from .output import prepare_output


def analyze_run(command, output_dir, *, xml_path=None, project_dir=None, cwd=None, stdin_file=None, live_port=None):
    """Collect if needed, export HTML, then replay with automatic value capture.

    Stops at each runtime error in GDB; continue/quit remain interactive.
    Ctrl+C aborts the workflow and never starts a new replay.
    """
    directory = Path(output_dir).resolve()
    source = Path(xml_path).resolve() if xml_path else None
    report = core.load_report(source, project_dir) if source else None
    command = list(command)
    if report:
        metadata = report['debug_command']
        if not command:
            command = metadata['target_args']
        base = metadata['base_args']
        cwd = cwd or base[base.index('--cwd') + 1]
        if stdin_file is None and '--stdin-file' in base:
            stdin_file = base[base.index('--stdin-file') + 1]
    if not command:
        raise ValueError('请在 -- 后提供程序及参数；已有 XML 时也可从配套采集记录自动读取')
    core.check_debug_environment()
    if not sys.stdin.isatty():
        raise ValueError('自动变量采集需要交互式 Linux / SSH 终端')
    print('预检查：确认 GDB 支持 Python，避免首次运行结束后才发现无法采集（最多 5 秒）。', flush=True)
    with ProcessSession() as processes:
        probe = processes.launch([shutil.which('gdb'), '-q', '-nx', '-nh', '-batch', '-ex', 'python import gdb'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            out, err = probe.communicate(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('GDB Python 预检查超过 5 秒，未启动目标程序') from exc
        if probe.returncode:
            raise ValueError('自动采集需要带 Python 支持的 GDB，未启动目标程序。\n' + out + err)
    if source:
        prepare_output(directory, ['report.html', 'report.html.tmp'],
                       [source, source.parent / 'run.json', stdin_file, Path(cwd or '.') / command[0]])
    else:
        print('步骤 1/3：首次运行 Valgrind，保存 XML。此流程随后会再次运行目标程序。', flush=True)
        collect_run(command, directory, cwd=cwd, stdin_file=stdin_file, live_port=live_port, project_dir=project_dir)
        source = directory / 'errors.xml'
    print('步骤 2/3：生成 HTML 报告。', flush=True)
    report = export_report(source, directory / 'report.html', project_dir=project_dir)
    print('报告: ' + str(directory / 'report.html'), flush=True)
    if not report['errors']:
        print('报告中没有已完整记录的内存错误，不启动复现采集。', flush=True)
        return 0
    print('步骤 3/3：重新运行并自动保存实际错误处的变量；无需网页点击或输入错误 ID。', flush=True)
    print('采集后停在 (gdb)：continue 继续到后续错误，quit 退出；Ctrl+C 终止整个流程。', flush=True)
    options = DebugOptions(command, cwd=cwd, stdin_file=stdin_file, auto_values=True,
                           capture_dir=directory / 'captures')
    return debug_error(report, report['errors'][0]['id'], options)
