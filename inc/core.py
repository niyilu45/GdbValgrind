#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Valgrind XML report browser and repeatable GDB/vgdb launcher (Python 3.9+)."""
from contextlib import ExitStack
import hashlib
import http.server
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import webbrowser
import xml.etree.ElementTree as ET
from .templates import HTML
from .capture import GDB_CAPTURE_SCRIPT
from .processes import ProcessSession, interrupt_scope
from .xmlstream import read_xml
from .commands import command_metadata
from .navigation import GDB_NAVIGATION_SCRIPT
from copy import copy
from .output import register_capture


def integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalized(text):
    return re.sub(r"0x[0-9a-fA-F]+", "<address>", text or "")


def diagnostic_label(text):
    """Ignore instance data in auxiliary memory descriptions, not error semantics."""
    text = normalized(text)
    text = re.sub(r'\b[\d,]+(?= bytes? (?:after|before|inside)\b)', '<offset>', text)
    text = re.sub(r'(\bblock of size )\d[\d,]*', r'\1<size>', text)
    text = re.sub(r'(\bThread\s+)\d+', r'\1<thread>', text)
    return text


class Sources:
    """Resolve only files within the explicitly supplied project directory."""
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root else None
        self.index = None
        self.cache = {}
        self.files = {}
        if self.root and not self.root.is_dir():
            raise ValueError("工程目录不存在: " + str(self.root))

    def resolve(self, frame):
        if not self.root or not frame.get("file"):
            return None
        original = PurePosixPath(frame.get("dir", "").replace("\\", "/")) / frame["file"].replace("\\", "/")
        candidates = [Path(str(original)), self.root / str(original)]
        parts = [p for p in original.parts if p not in ("/", "..", ".")]
        candidates.extend(self.root.joinpath(*parts[i:]) for i in range(len(parts)))
        # Prefer the longest matching suffix after a checkout has moved.
        for candidate in candidates:
            path = candidate.resolve()
            if path.is_relative_to(self.root) and path.is_file():
                return path
        if self.index is None:
            self.index = {}
            for directory, dirs, files in os.walk(self.root):
                dirs[:] = [d for d in dirs if d not in {".git", ".svn", "node_modules", ".venv", "__pycache__"}]
                for name in files:
                    self.index.setdefault(name, []).append(Path(directory) / name)
        matches = [p.resolve() for p in self.index.get(parts[-1], [])] if parts else []
        matches = [p for p in matches if p.is_relative_to(self.root)]
        return matches[0] if len(matches) == 1 else None

    def enrich(self, frame):
        path = self.resolve(frame)
        if not path:
            frame["source_note"] = "源码未找到或文件名不唯一" if self.root else "未指定工程目录"
            return
        frame["local_file"] = str(path)
        try:
            if path not in self.cache:
                self.cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = self.cache[path]
            key = hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:16]
            self.files[key] = {'path': str(path), 'lines': lines}
            frame['source_file_id'] = key
            line = integer(frame.get("line"))
            if not 1 <= line <= len(lines):
                frame["source_note"] = "XML 行号超出源码范围；请核对代码版本"
                return
            frame["source"] = [{"number": i + 1, "text": lines[i]} for i in range(max(0, line - 11), min(len(lines), line + 10))]
        except OSError as exc:
            frame["source_note"] = "无法读取源码: " + str(exc)


def frame_key(frame, function_only=False):
    if frame.get("file") and integer(frame.get("line")) > 0:
        return tuple(frame.get(k, "") for k in ("obj", "fn", "dir", "file", "line"))
    if function_only and frame.get('fn') and frame.get('fn') != '???':
        # Named library frames often have no line information. ASLR and the
        # instruction offset within that same function are not distinct paths.
        return (frame.get('obj', ''), frame['fn'])
    # Without symbols, keep the address: merging different unknown sites is worse
    # than missing a duplicate across ASLR runs.
    return (frame.get("obj", ""), frame.get("fn", ""), frame.get("ip", ""))


def load_report(xml_path, project=None, allow_partial=True):
    stream = read_xml(xml_path, allow_partial)
    report = report_from_root(stream.root, xml_path, project, xml_complete=stream.complete)
    report['debug_command'] = command_metadata(stream.root, xml_path, project)
    return report


def report_from_root(root, xml_path, project=None, xml_complete=True):
    counts = {}
    for pair in root.findall("errorcounts/pair"):
        uid = pair.findtext("unique", "")
        counts[uid] = max(counts.get(uid, 0), integer(pair.findtext("count"), 1))
    grouped, seen = {}, set()
    for error in root.findall("error"):
        uid = error.findtext("unique", "")
        if uid and uid in seen:
            continue
        seen.add(uid)
        kind = error.findtext("kind", "Unknown")
        what = error.findtext("what") or error.findtext("xwhat/text") or kind
        stacks = []
        label = "错误调用栈"
        for child in error:
            if child.tag == "auxwhat":
                label = child.text or "关联调用栈"
            elif child.tag == "xauxwhat":
                label = child.findtext("text", "关联调用栈")
            elif child.tag == "stack":
                stacks.append({"label": label, "frames": [{k: f.findtext(k, "") for k in ("ip", "obj", "fn", "dir", "file", "line")} for f in child.findall("frame")]})
                label = "关联调用栈"
            elif child.tag == "origin":
                for stack in child.findall("stack"):
                    stacks.append({"label": child.findtext("what", "未初始化值来源"), "frames": [{k: f.findtext(k, "") for k in ("ip", "obj", "fn", "dir", "file", "line")} for f in stack.findall("frame")]})
        signature = [kind, "" if kind.startswith("Leak_") else normalized(what),
                     [(diagnostic_label(s["label"]), [frame_key(f, kind in ('UninitCondition', 'UninitValue')) for f in s["frames"]]) for s in stacks]]
        if not any(s["frames"] for s in stacks):
            signature.append(uid or ET.tostring(error, encoding="unicode"))
        key = json.dumps(signature, ensure_ascii=False, sort_keys=True)
        if key not in grouped:
            grouped[key] = {"id": hashlib.sha256(key.encode()).hexdigest()[:16], "kind": kind,
                            "what": what, "stacks": stacks, "count": 0, "records": 0,
                            "unique_ids": [], "leaked_bytes": 0, "leaked_blocks": 0}
        item = grouped[key]
        item["count"] += max(1, counts.get(uid, 1))
        item["records"] += 1
        item["unique_ids"].append(uid)
        item["leaked_bytes"] += integer(error.findtext("xwhat/leakedbytes"))
        item["leaked_blocks"] += integer(error.findtext("xwhat/leakedblocks"))
    sources = Sources(project)
    errors = list(grouped.values())
    for error in errors:
        for stack in error["stacks"]:
            for frame in stack["frames"]:
                sources.enrich(frame)
    statuses = root.findall("status/state")
    finished = bool(xml_complete and statuses and statuses[-1].text == "FINISHED")
    return {"xml": str(Path(xml_path).resolve()), "project": str(sources.root or ""), "source_files": sources.files,
            "errors": errors, "occurrences": sum(e["count"] for e in errors),
            "finished": finished, "xml_complete": xml_complete,
            "counts_complete": finished and all(uid in counts for e in errors for uid in e['unique_ids']),
            "fatal": root.findtext("fatal_signal/signame", ""),
            "tool": root.findtext("tool", "memcheck")}


def pick_frame(error, selected=None):
    if selected is not None:
        try:
            si, fi = map(int, selected.split(":"))
            if si < 0 or fi < 0:
                raise ValueError()
            frame = error["stacks"][si]["frames"][fi]
        except (ValueError, IndexError):
            raise ValueError("--frame 必须是有效的 调用栈序号:帧序号（从 0 开始）")
        if not ((frame.get("file") and integer(frame.get("line")) > 0) or frame.get("fn")):
            raise ValueError("所选帧没有源码位置或函数符号，请重新使用 -g 编译")
        return frame
    frames = error["stacks"][0]["frames"] if error["stacks"] else []
    for frame in frames:
        if frame.get("local_file") and integer(frame.get("line")) > 0:
            return frame
    for frame in frames:
        if frame.get("file") and integer(frame.get("line")) > 0 and not frame["file"].startswith(("vg_", "m_")):
            return frame
    for frame in frames:
        if frame.get("fn") and not frame["fn"].startswith(("vg", "_vgr")):
            return frame
    raise ValueError("错误缺少可用源码位置或函数符号，请使用 -g -O0 重新编译；不复用旧进程绝对地址")


def gdb_quote(value):
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("GDB 路径或符号包含非法控制字符")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def breakpoint_command(frame):
    if frame.get("file") and integer(frame.get("line")) > 0:
        # Use the original debug-info filename, not the relocated checkout path.
        source = str(PurePosixPath(frame.get("dir", "")) / frame["file"])
        return "break -source " + gdb_quote(source) + " -line " + str(int(frame["line"]))
    return "break -function " + gdb_quote(frame["fn"])


def debug_commands(frame, pid, prefix, vgdb, project=None, stop_on_error=False, capture_script=None):
    commands = ["set pagination off", "set confirm off", "set breakpoint pending on", "set remotetimeout 30"]
    if project:
        commands.append("directory " + gdb_quote(str(Path(project).resolve())))
    if frame.get("local_file") and frame.get("dir"):
        commands.append("set substitute-path " + gdb_quote(frame["dir"]) + " " + gdb_quote(str(Path(frame["local_file"]).parent)))
    commands.append("target remote | " + shlex.join([vgdb, "--vgdb-prefix=" + prefix, "--pid=" + str(pid), "--wait=10"]))
    if capture_script:
        commands += ["set may-call-functions off", "set print elements 32", "set print repeats 8",
                     "python exec(compile(open(" + repr(str(capture_script)) + ", encoding='utf-8').read(), 'aivalgrind-capture', 'exec'))"]
    else:
        commands.append(breakpoint_command(frame))
    commands += ["monitor v.set vgdb-error " + ("1" if stop_on_error or capture_script else "999999999"), "continue"]
    return commands


def prepare_capture(directory, requested_error_id):
    """Each invocation gets its own directory; no previous capture is overwritten."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix="session-", dir=str(directory)))
    register_capture(session)
    config = {"directory": str(session), "requested_error_id": requested_error_id}
    script = session / "capture.py"
    script.write_text("_capture_config = " + repr(config) + "\n" + GDB_CAPTURE_SCRIPT, encoding="utf-8")
    return session, script




def check_debug_environment():
    if sys.platform != "linux":
        raise ValueError("联合调试需要 Linux（Windows 请在 WSL 内运行本脚本）；HTML 转换不受此限制")
    for name in ("valgrind", "vgdb", "gdb"):
        if not shutil.which(name):
            raise ValueError("缺少命令: " + name + "；请安装 Valgrind 和 GDB")


def run_debug(error, args):
    check_debug_environment()
    args = copy(args)
    while True:
        with ProcessSession() as processes:
            result = _run_debug(error, args, processes)
        if not isinstance(result, dict):
            return result
        error = next(e for e in args.navigation_errors if e['id'] == result['id'])
        args.frame = result['frame']
        # Keep capture enabled when requested, but navigate to this source location.
        args.navigation_force = True


def prepare_navigation(folder, errors, error, frame, auto_values):
    entries = []
    for order, item in enumerate(errors, 1):
        frames = {}
        for si, stack in enumerate(item['stacks']):
            for fi, candidate in enumerate(stack['frames']):
                if candidate.get('fn') or (candidate.get('file') and integer(candidate.get('line')) > 0):
                    frames['%d:%d' % (si, fi)] = breakpoint_command(candidate)[6:]
        try:
            default = breakpoint_command(frame if item['id'] == error['id'] and frame else pick_frame(item))[6:]
        except ValueError:
            default = None
        entries.append({'id': item['id'], 'order': order, 'frames': frames, 'default': default})
    restart = Path(folder) / 'restart.json'
    config = {'entries': entries, 'initial': error['id'], 'auto_values': auto_values, 'restart_file': str(restart)}
    script = Path(folder) / 'navigation.py'
    script.write_text('_navigation_config = ' + repr(config) + '\n' + GDB_NAVIGATION_SCRIPT, encoding='utf-8')
    return script, restart


def _run_debug(error, args, processes):
    if not sys.stdin.isatty():
        raise ValueError("GDB 需要交互终端；请在终端中运行 debug 或 serve")
    auto_values = getattr(args, "auto_values", False)
    try:
        frame = pick_frame(error, args.frame)
    except ValueError:
        if not auto_values or args.frame is not None:
            raise
        frame = {}
    if not auto_values:
        breakpoint_command(frame)  # Validate before launching the target.
    navigation_enabled = bool(getattr(args, 'navigation_errors', None))
    if auto_values or navigation_enabled:
        debugger_path = shutil.which('gdb')
        print('正在检查 GDB 的 Python 支持（最多等待 5 秒）: ' + debugger_path, flush=True)
        probe = processes.launch([debugger_path, "-q", "-nx", "-nh", "-iex", "set auto-load off", "-batch", "-ex", "python import gdb, sys; print('GDB: ' + gdb.VERSION + '; embedded Python: ' + sys.version.split()[0])"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            probe_output, probe_error = probe.communicate(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('GDB 启动检查超过 5 秒，已取消调试；请在服务器单独运行 gdb --version 检查安装和启动环境。') from exc
        if probe.returncode:
            diagnostic = (probe_output + '\n' + probe_error).strip()
            unsupported = 'python scripting is not supported' in diagnostic.lower()
            if unsupported and not auto_values:
                navigation_enabled = False
                print('当前 GDB 未编译 Python 支持，已切换为普通断点调试。', flush=True)
                print('可以使用 bt、info locals、print 和 continue；网页的 aiv-goto 继续命令在此会话不可用。', flush=True)
                print('若要切换报告错误，请先输入 quit，再在 Shell 执行该错误的完整启动命令。', flush=True)
            elif unsupported:
                raise ValueError('当前 GDB 未编译 Python 支持，无法使用 --auto-values 自动采集。请更换带 Python 支持的 GDB 并确保 PATH 优先找到它，或去掉 --auto-values 使用普通断点调试。仅安装 Python 不能为这个 GDB 增加支持。\nGDB: ' + debugger_path + '\n' + diagnostic)
            else:
                raise ValueError('GDB Python 启动检查失败，尚未启动目标程序。\nGDB: ' + debugger_path + '\n' + diagnostic)
        else:
            print('GDB Python 支持检查通过。', flush=True)
            if probe_output.strip():
                print(probe_output.strip(), flush=True)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("请在 -- 后提供可执行程序及参数")
    cwd = Path(args.cwd or os.getcwd()).resolve()
    if not cwd.is_dir():
        raise ValueError("运行目录不存在: " + str(cwd))
    executable = Path(command[0])
    if not executable.is_absolute():
        local = cwd / executable
        executable = local if local.is_file() else Path(shutil.which(command[0]) or str(local))
    command[0] = str(executable.resolve())
    if not executable.is_file():
        raise ValueError("可执行程序不存在: " + str(executable))
    print("\n调试 " + error["id"] + " · " + error["what"], flush=True)
    capture_script = None
    if auto_values:
        session, capture_script = prepare_capture(args.capture_dir, error["id"])
        print("自动采集模式：跳过源码断点，在实际内存错误处暂停并保存变量。\n采集目录: " + str(session), flush=True)
    else:
        print("断点: " + breakpoint_command(frame), flush=True)
    if error["kind"].startswith("Leak_"):
        print("泄漏记录指向分配调用栈；可在 GDB 中执行 monitor leak_check full reachable any。", flush=True)
    with tempfile.TemporaryDirectory(prefix="aivalgrind-") as folder, ExitStack() as resources:
        prefix = str(Path(folder) / "vgdb")
        log_path = Path(folder) / "valgrind.log"
        target_input = resources.enter_context(open(args.stdin_file, "rb")) if args.stdin_file else subprocess.DEVNULL
        # Ctrl+C reaches the supervisor, which owns separate tool process groups.
        print('正在启动 Valgrind 和目标程序，随后连接 GDB…', flush=True)
        vg = processes.launch([shutil.which("valgrind"), "--tool=memcheck", "--leak-check=full",
                               "--show-leak-kinds=all", "--track-origins=yes", "--vgdb=yes",
                               "--vgdb-error=0", "--vgdb-prefix=" + prefix,
                               "--log-file=" + str(log_path)] + (["--read-var-info=yes"] if auto_values else []) + command,
                              cwd=cwd, stdin=target_input, start_new_session=True)
        try:
            commands = debug_commands(frame, vg.pid, prefix, shutil.which("vgdb"), args.project_dir, args.stop_on_error, capture_script)
            restart = None
            if navigation_enabled:
                navigation, restart = prepare_navigation(folder, args.navigation_errors, error, frame,
                    auto_values and not getattr(args, 'navigation_force', False))
                if not auto_values:
                    commands.remove(breakpoint_command(frame))
                commands.insert(-1, "python exec(compile(open(" + repr(str(navigation)) + ", encoding='utf-8').read(), 'aivalgrind-navigation', 'exec'))")
            script = Path(folder) / "session.gdb"
            script.write_text("\n".join(commands) + "\n", encoding="utf-8")
            # -nx/-nh avoids executing unexpected personal GDB startup files.
            print('正在连接 GDB；连接后程序将运行到断点，耗时取决于程序执行路径。Ctrl+C 可退出并清理进程。', flush=True)
            gdb = processes.launch([shutil.which("gdb"), "-q", "-nx", "-nh", "-iex", "set auto-load off",
                                    "-x", str(script), "--args"] + command, cwd=cwd)
            while True:
                try:
                    result = gdb.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if auto_values:
                        failure = session / 'connection-error.txt'
                        if failure.exists():
                            raise ValueError(failure.read_text(encoding='utf-8') + '\n诊断目录: ' + str(session))
                        target_result = vg.poll()
                        if isinstance(target_result, int):
                            print('Valgrind/目标进程已退出（退出码 %s），结束自动采集并清理 GDB。日志: %s' % (target_result, session / 'valgrind.log'), flush=True)
                            return target_result
                    continue
            if auto_values and (session / 'connection-error.txt').exists():
                raise ValueError((session / 'connection-error.txt').read_text(encoding='utf-8'))
            if restart is not None and restart.exists() and result == 0:
                request = json.loads(restart.read_text(encoding='utf-8'))
                selected = next(e for e in args.navigation_errors if e['id'] == request['id'])
                pick_frame(selected, request['frame'])
                return request
            return result
        finally:
            processes.close()
            if log_path.exists():
                if auto_values:
                    shutil.copyfile(log_path, session / "valgrind.log")
                if sys.exc_info()[0] is None:
                    with log_path.open('rb') as log:
                        log.seek(max(0, log_path.stat().st_size - 65536))
                        print(log.read().decode(errors='replace'), flush=True)


def render_html(report, token=""):
    # Full source already lives once in source_files. Do not repeat the same
    # 21-line excerpts for every frame of every error in the offline artifact.
    files = report.get('source_files', {})
    errors = []
    for error in report['errors']:
        item = {k: v for k, v in error.items() if k != 'unique_ids'}
        item['stacks'] = [{**stack, 'frames': [
            {k: v for k, v in frame.items() if k != 'source' or frame.get('source_file_id') not in files}
            for frame in stack['frames']]} for stack in error['stacks']]
        errors.append(item)
    payload = json.dumps({**report, 'errors': errors, "token": token}, ensure_ascii=False, separators=(',', ': ')).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return HTML.replace("__REPORT_DATA__", payload)


class DebugServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, report, port=0, enabled=False):
        self.report = report
        self.token = secrets.token_urlsafe(32)
        self.enabled = enabled
        self.auto_values = False
        self.pending = queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.state = {"busy": False, "message": "等待选择错误"}
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)


class Handler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get("Host") == "127.0.0.1:" + str(self.server.server_port)

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {"message": "Host 不允许"})
        if self.path == "/":
            return self.reply(200, render_html(self.server.report, self.server.token if self.server.enabled else ""), "text/html; charset=utf-8")
        if self.path == "/api/status" and secrets.compare_digest(self.headers.get("X-Debug-Token", ""), self.server.token):
            with self.server.lock:
                state = dict(self.server.state)
            return self.reply(200, state)
        self.reply(404, {"message": "未找到"})

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") != self.server.origin or not secrets.compare_digest(self.headers.get("X-Debug-Token", ""), self.server.token):
            return self.reply(403, {"message": "仅允许本机报告页面发起调试"})
        if self.path != "/api/debug" or not self.server.enabled:
            return self.reply(404, {"message": "未启用调试"})
        size = integer(self.headers.get("Content-Length"))
        if not 0 < size <= 4096:
            return self.reply(400, {"message": "请求长度无效"})
        try:
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            error = next(e for e in self.server.report["errors"] if e["id"] == data.get("id"))
            selected = data.get("frame")
            if selected is not None and not isinstance(selected, str):
                raise ValueError()
            if not self.server.auto_values or selected is not None:
                breakpoint_command(pick_frame(error, selected))
        except (ValueError, StopIteration, KeyError):
            return self.reply(400, {"message": "错误或断点位置无效"})
        with self.server.lock:
            if self.server.state["busy"]:
                busy = True
            else:
                busy = False
                self.server.state = {"busy": True, "message": "已启动，请切换到运行服务的终端操作 GDB"}
                self.server.pending.put_nowait((error, selected))
        if busy:
            return self.reply(409, {"message": "已有调试会话，请先在终端退出 GDB"})
        self.reply(202, {"message": "已提交调试，请切换到终端。"})


def serve(report, args):
    with interrupt_scope():
        return _serve(report, args)


def browsing_instructions(server):
    lines = ["浏览报告: " + server.origin + "/"]
    if server.enabled:
        button = '启动并采集变量' if server.auto_values else '在终端启动 GDB'
        lines += [
            '当前模式：网页可启动调试。请保持这个终端运行。',
            '1. 在网页选中一条错误，点击“' + button + '”。',
            '2. 切回这个终端，等待出现 (gdb) 提示符，再输入下面的 GDB 命令。',
        ]
    else:
        lines += [
            '当前模式：仅浏览报告，尚未启动 GDB。这个终端正在提供网页服务，不能在这里直接输入调试命令。',
            '1. 在网页选中一条错误，点击“生成调试命令”，再点击“复制命令”。',
            '2. 另开一个连接同一台 Linux 服务器的 SSH 终端，在普通 Shell 提示符（通常是 $ 或 #）后粘贴完整命令并回车。',
            '   命令已带入生成报告时的工程目录和程序信息；不要在本地 Windows 终端执行。',
            '3. 等待出现 (gdb) 提示符，表示调试已启动，再输入下面的 GDB 命令。',
        ]
        if not server.report.get('debug_command', {}).get('ready'):
            lines.append('   注意：当前报告缺少原程序信息，网页无法生成完整启动命令；请使用包含程序参数的 Valgrind XML 或配套的 collect 采集记录重新生成报告。')
    lines += [
        '',
        '出现 (gdb) 后可输入（只输入命令，不要输入提示符本身）：',
        '  bt                 查看调用栈',
        '  info locals        查看当前栈帧的局部变量',
        '  print 变量名       查看指定变量，请将“变量名”替换为实际名称',
        '  continue           继续执行，直到下一个断点、内存错误暂停或程序结束',
        '  quit               退出这次调试并清理目标进程',
        '切换错误：在网页选中另一条错误，点击“复制继续命令”，粘贴到已有会话的 (gdb) 后执行。',
        '  aiv-goto 只能在 (gdb) 中使用，不能在普通 Shell 中执行；回到之前的位置时按提示输入 y 重跑或 n 忽略。',
        '',
        '远程 SSH 浏览：若本机浏览器无法打开上述地址，在本机另开终端建立转发（将 用户名@服务器 替换为实际 SSH 登录地址）：',
        '  ssh -N -L {0}:127.0.0.1:{0} 用户名@服务器'.format(server.server_port),
        '保持转发终端运行，然后在本机浏览器打开 ' + server.origin + '/',
        '本服务只提供网页，不会另存 HTML 文件；需要离线 HTML 时使用 report 命令。',
        'Ctrl+C 停止所在终端中的服务或整个调试会话，不是 GDB 暂停操作。',
    ]
    return '\n'.join(lines)


def _serve(report, args):
    args.navigation_errors = report['errors']
    enabled = bool(args.command)
    if enabled:
        check_debug_environment()
        if not sys.stdin.isatty():
            raise ValueError("启用网页调试时请从交互终端启动服务")
    server = DebugServer({**report, "auto_values": bool(enabled and args.auto_values)}, args.port, enabled)
    server.auto_values = args.auto_values
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        print(browsing_instructions(server), flush=True)
        if args.open:
            webbrowser.open(server.origin + "/")
        while True:
            try:
                error, selected = server.pending.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                args.frame = selected
                result = run_debug(error, args)
                message = "调试已结束（退出码 " + str(result) + "），可选择下一条"
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                message = "调试失败: " + str(exc)
                print(message, file=sys.stderr)
            finally:
                with server.lock:
                    server.state = {"busy": False, "message": locals().get("message", "调试已停止")}
    finally:
        with interrupt_scope(signal.SIG_IGN):
            if thread.is_alive():
                server.shutdown()
            server.server_close()
            thread.join(timeout=2)
