"""One-command or saved-report entry to automatic value capture."""
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import json
import re
from .capture_updates import SCRIPT as CAPTURE_UPDATES

from . import core
from .api import DebugOptions, debug_error
from .collect import collect_run
from .processes import ProcessSession
from .output import prepare_output, prepare_capture_output
from .terminal import ReportProgress
from .store import load_replay


def analyze_run(command, output_dir, *, xml_path=None, project_dir=None, cwd=None, stdin_file=None, live_port=None,
                plain_terminal=False, output_mode='pty', runtime_info=None, pause_on_error=False, step3_only=False, base_report=None):
    """Collect if needed, load error locations, then replay with value capture.

    Capture and continue by default; pause_on_error opts into interactive GDB.
    Ctrl+C aborts the workflow and never starts a new replay.
    """
    directory = Path(output_dir).resolve()
    source = Path(xml_path).resolve() if xml_path else None
    print('analyze 流程版本: SQLite 分页 / 步骤 2 自动生成完整 HTML / 步骤 3 默认自动继续\n运行代码: ' + str(Path(__file__).resolve()),flush=True)
    if step3_only and source is None:
        raise ValueError("--step3-only 需要 --xml 指定已有报告")
    base_path = Path(base_report).resolve() if base_report else (source.parent / 'report.html' if step3_only else None)
    base_html = None
    if step3_only:
        if not base_path.is_file():
            raise ValueError('需要步骤二 HTML，请使用 --base-report 指定：' + str(base_path))
        base_html = base_path.read_text(encoding='utf-8')
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
    if source and not step3_only:
        prepare_output(directory, (['full-report.html', 'full-report.html.tmp', 'capture-updates.js', 'capture-updates.js.tmp'] if step3_only else ['report.html', 'report.html.tmp', 'full-report.html', 'full-report.html.tmp', 'capture-updates.js', 'capture-updates.js.tmp']),
                       [source, source.parent / 'run.json', base_path, stdin_file, Path(cwd or '.') / command[0]])
    elif not source:
        print('步骤 1/3：首次运行 Valgrind，保存 XML。此流程随后会再次运行目标程序。', flush=True)
        collect_run(command, directory, cwd=cwd, stdin_file=stdin_file, live_port=live_port, project_dir=project_dir,
                    plain_terminal=plain_terminal, output_mode=output_mode, runtime_info=runtime_info)
        source = directory / 'errors.xml'
    if not step3_only:
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
    if not step3_only:
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
    base_html = base_html or (directory / 'report.html').read_text(encoding='utf-8')
    prepare_capture_output(directory)
    previous = set((directory / 'captures').glob('session-*/error-*.txt'))
    stop = threading.Event()
    last_snapshot = None
    def update(live):
        nonlocal last_snapshot
        snapshot = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                         for p in sorted((directory / 'captures').glob('session-*/error-*.txt')) if p not in previous)
        if live and snapshot == last_snapshot:
            return
        save_combined_report(report, directory, base_html=base_html, previous=previous, live=live)
        last_snapshot = snapshot
    def refresh():
        while not stop.wait(2):
            try:
                update(True)
            except OSError as exc:
                print('合并报告暂未更新：' + str(exc), file=sys.stderr, flush=True)
    update(True)
    print('实时合并报告（内容增量更新，不刷新页面）：' + str(directory / 'full-report.html'), flush=True)
    worker = threading.Thread(target=refresh, name='aivalgrind-combined-report', daemon=True)
    worker.start()
    try:
        return debug_error(report, report['errors'][0]['id'], options)
    finally:
        stop.set()
        worker.join()
        with ReportProgress('合并步骤三现场报告') as progress:
            progress('写入完整报告和已保存的变量现场')
            update(False)
        print('合并报告：' + str(directory / 'full-report.html'), flush=True)


def save_combined_report(report, directory, *, base_html=None, previous=(), live=False):
    """Update the sidecar only; initialize the report viewer once."""
    items = []
    for path in sorted((directory / 'captures').glob('session-*/error-*.txt')):
        if path in previous:
            continue
        identity = str(path.relative_to(directory))
        text = path.read_text(encoding='utf-8', errors='replace')
        item = {'id':identity, 'text':text}
        try:
            item['snapshot'] = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        items.append(item)
    update_file = directory / 'capture-updates.js.tmp'
    update_file.write_text('window.aivCaptureUpdate(' + json.dumps({'live':live,'items':items},ensure_ascii=True).replace('<','\\u003c') + ');', encoding='utf-8')
    update_file.replace(directory / 'capture-updates.js')
    refresh_capture_report(directory, base_html=base_html, report=report)


def refresh_capture_report(directory, *, base_html=None, report=None):
    """Upgrade the viewer without rerunning the target or rewriting captured values."""
    directory = Path(directory).resolve()
    if not (directory / 'capture-updates.js').is_file():
        raise ValueError('缺少步骤三变量文件 capture-updates.js：' + str(directory))
    output = directory / 'full-report.html'
    if output.exists():
        content = output.read_text(encoding='utf-8')
    elif base_html:
        content = base_html
    elif report is not None:
        content = core.render_html(report, source_base=None)
    else:
        content = (directory / 'report.html').read_text(encoding='utf-8')
    if 'aiv-stack-captures-v12' in content:
        return
    from .templates import SOURCE_VIEW_STYLE, STACK_SCROLL_FUNCTION, SELECT_ERROR_FUNCTION, FILTER_STATE_SCRIPT
    if 'id="report-data"' in content:
        if '// aiv-filter-state-v1' not in content:
            content = content.replace('let indexed=[],kindCounts=new Map();', FILTER_STATE_SCRIPT + '\nlet indexed=[],kindCounts=new Map();', 1)
            content = content.replace('function list(reset=true){', 'function list(reset=true){\n saveFilters();', 1)
            content = content.replace("$('resetFilters').onclick=()=>{kind='';", "$('resetFilters').onclick=()=>{savedFilters.variables=false;kind='';")
        content = re.sub(r'^function scrollToErrorStack\(\).*$', '', content, flags=re.M)
        content = re.sub(r'^function selectError\(e(?:,navigate=true)?\).*$',
                         lambda match: STACK_SCROLL_FUNCTION + '\n' + SELECT_ERROR_FUNCTION,
                         content, flags=re.M)
        if 'd.dataset.hasSource=' not in content:
            content = content.replace('d.dataset.frameKey=frameKey;',
                                      "d.dataset.frameKey=frameKey;d.dataset.hasSource=f.source?'yes':'no';")
        content = content.replace('</head>', '<style>' + SOURCE_VIEW_STYLE + '</style></head>', 1)
    content = re.sub(r'<section id="capture-results".*?</section>\s*(?:<script>.*?</script>)?', '', content, flags=re.S)
    content = content.replace('<a href="#capture-results">查看步骤三变量现场</a>', '')
    appendix = '<section id="capture-results" style="padding:24px"><h2>步骤三变量状态</h2><p>变量显示在对应错误的主调用栈帧下方。</p><p id="capture-status">正在读取变量数据…</p></section>' + CAPTURE_UPDATES
    content = content.replace('</body>', appendix + '</body>')
    content = content.replace('<body>', '<body><a href="#capture-results">查看步骤三变量现场</a>', 1)
    temporary = directory / 'full-report.html.tmp'
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(directory / 'full-report.html')
