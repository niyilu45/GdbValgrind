"""One-command or saved-report entry to automatic value capture."""
from pathlib import Path
import shutil
import subprocess
import sys

from . import core
from .api import DebugOptions, debug_error
from .collect import collect_run
from .processes import ProcessSession
from .output import prepare_output
from .terminal import ReportProgress
from .store import load_replay


def analyze_run(command, output_dir, *, xml_path=None, project_dir=None, cwd=None, stdin_file=None, live_port=None,
                plain_terminal=False, output_mode='pty', runtime_info=None, pause_on_error=False):
    """Collect if needed, load error locations, then replay with value capture.

    Capture and continue by default; pause_on_error opts into interactive GDB.
    Ctrl+C aborts the workflow and never starts a new replay.
    """
    directory = Path(output_dir).resolve()
    source = Path(xml_path).resolve() if xml_path else None
    print('analyze 流程版本: SQLite 分页 / 步骤 2 自动生成完整 HTML / 步骤 3 默认自动继续\n运行代码: ' + str(Path(__file__).resolve()),flush=True)
    report = None
    if source:
        print('正在解析已有 XML 和错误位置（不等待源码或 blame）……', flush=True)
        with ReportProgress('读取已有报告') as progress:
            report = core.load_report(source,progress=progress)
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
    if pause_on_error and not sys.stdin.isatty():
        raise ValueError('--pause-on-error 需要交互式 Linux / SSH 终端')
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
        collect_run(command, directory, cwd=cwd, stdin_file=stdin_file, live_port=live_port, project_dir=project_dir,
                    plain_terminal=plain_terminal, output_mode=output_mode, runtime_info=runtime_info)
        source = directory / 'errors.xml'
    print('步骤 2/3：生成完整 HTML 报告（含错误堆栈；提供工程目录时加载源码和 blame）。', flush=True)
    if report is None:
        with ReportProgress('步骤 2/3：准备复现位置') as progress:
            database=directory/'results.sqlite3'
            if database.is_file():
                progress('读取 SQLite 复现位置')
                report=load_replay(database,source,progress,include_enriched=True)
            else:
                report=core.load_report(source,progress=progress)
    report['project'] = str(Path(project_dir).resolve()) if project_dir else ''
    with ReportProgress('步骤 2/3：生成完整 HTML') as progress:
        if project_dir:
            core.enrich_parallel(report, project_dir, workers=4, progress=progress,reuse=True)
        progress('序列化并写入 HTML')
        temporary = directory / 'report.html.tmp'
        core.save_report(report, temporary)
        temporary.replace(directory / 'report.html')
    print('完整报告已生成，可直接用浏览器打开：' + str(directory / 'report.html'), flush=True)
    if not report['errors']:
        print('报告中没有已完整记录的内存错误，不启动复现采集。', flush=True)
        return 0
    print('步骤 3/3：重新运行并自动保存实际错误处的变量；无需网页点击或输入错误 ID。', flush=True)
    if pause_on_error:
        print('采集后停在 (gdb)：continue 继续到后续错误，quit 退出；Ctrl+C 终止整个流程。', flush=True)
    else:
        print('保存现场后自动继续，程序结束后自动退出；无需输入 continue。Ctrl+C 终止整个流程。', flush=True)
    options = DebugOptions(command, cwd=cwd, stdin_file=stdin_file, auto_values=True,
                           capture_dir=directory / 'captures', auto_continue=not pause_on_error)
    return debug_error(report, report['errors'][0]['id'], options)
