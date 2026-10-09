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


def integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalized(text):
    return re.sub(r"0x[0-9a-fA-F]+", "<address>", text or "")


class Sources:
    """Resolve only files within the explicitly supplied project directory."""
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root else None
        self.index = None
        self.cache = {}
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
            line = integer(frame.get("line"))
            if not 1 <= line <= len(lines):
                frame["source_note"] = "XML 行号超出源码范围；请核对代码版本"
                return
            frame["source"] = [{"number": i + 1, "text": lines[i]} for i in range(max(0, line - 11), min(len(lines), line + 10))]
        except OSError as exc:
            frame["source_note"] = "无法读取源码: " + str(exc)


def frame_key(frame):
    if frame.get("file") and integer(frame.get("line")) > 0:
        return tuple(frame.get(k, "") for k in ("obj", "fn", "dir", "file", "line"))
    # Without symbols, keep the address: merging different unknown sites is worse
    # than missing a duplicate across ASLR runs.
    return (frame.get("obj", ""), frame.get("fn", ""), frame.get("ip", ""))


def load_report(xml_path, project=None, allow_partial=True):
    stream = read_xml(xml_path, allow_partial)
    return report_from_root(stream.root, xml_path, project, xml_complete=stream.complete)


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
                     [(normalized(s["label"]), [frame_key(f) for f in s["frames"]]) for s in stacks]]
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
    return {"xml": str(Path(xml_path).resolve()), "project": str(sources.root or ""),
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
    with ProcessSession() as processes:
        return _run_debug(error, args, processes)


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
    else:
        probe = processes.launch([shutil.which("gdb"), "-q", "-nx", "-nh", "-batch", "-ex", "python import gdb"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _, probe_error = probe.communicate(timeout=15)
        if probe.returncode:
            raise ValueError("--auto-values 需要启用了 Python 支持的 GDB: " + probe_error.strip())
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
        vg = processes.launch([shutil.which("valgrind"), "--tool=memcheck", "--leak-check=full",
                               "--show-leak-kinds=all", "--track-origins=yes", "--vgdb=yes",
                               "--vgdb-error=0", "--vgdb-prefix=" + prefix,
                               "--log-file=" + str(log_path)] + (["--read-var-info=yes"] if auto_values else []) + command,
                              cwd=cwd, stdin=target_input, start_new_session=True)
        try:
            commands = debug_commands(frame, vg.pid, prefix, shutil.which("vgdb"), args.project_dir, args.stop_on_error, capture_script)
            script = Path(folder) / "session.gdb"
            script.write_text("\n".join(commands) + "\n", encoding="utf-8")
            # -nx/-nh avoids executing unexpected personal GDB startup files.
            gdb = processes.launch([shutil.which("gdb"), "-q", "-nx", "-nh", "-iex", "set auto-load off",
                                    "-x", str(script), "--args"] + command, cwd=cwd)
            while True:
                try:
                    result = gdb.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
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
    payload = json.dumps({**report, "token": token}, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
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


def _serve(report, args):
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
        print("浏览报告: " + server.origin + "/", flush=True)
        print("点击网页调试后，在此终端使用 GDB；退出 GDB 后可选择下一条。Ctrl+C 停止服务。", flush=True)
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
