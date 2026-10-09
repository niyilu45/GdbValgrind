"""Bounded shutdown and reaping of owned Linux process groups."""
import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager


@contextmanager
def interrupt_scope(handler=None):
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('调试和服务必须从主线程调用，以便处理 Ctrl+C')
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    saved = {}
    try:
        for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
            sig = getattr(signal, name, None)
            if sig is not None:
                saved[sig] = signal.signal(sig, interrupt if handler is None else handler)
        yield
    finally:
        for sig, previous in saved.items():
            signal.signal(sig, previous)


class ProcessSession:
    def __init__(self, grace=2.0, kill_timeout=2.0):
        self.children = []
        self.grace, self.kill_timeout = grace, kill_timeout
        self.closed = False
        self.libc = None
        self.old_subreaper = None
        self.terminal = None

    def __enter__(self):
        self.signals = interrupt_scope()
        self.signals.__enter__()
        try:
            if sys.platform == 'linux':
                if sys.stdin.isatty():
                    import termios
                    try:
                        self.terminal = (sys.stdin.fileno(), termios.tcgetattr(sys.stdin.fileno()))
                    except (OSError, termios.error):
                        pass
                self.libc = ctypes.CDLL(None, use_errno=True)
                previous = ctypes.c_int()
                if self.libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
                    raise OSError(ctypes.get_errno(), '无法读取子进程回收设置')
                if self.libc.prctl(36, 1, 0, 0, 0) != 0:
                    raise OSError(ctypes.get_errno(), '无法启用子进程回收')
                self.old_subreaper = previous.value
        except BaseException:
            self.signals.__exit__(*sys.exc_info())
            raise
        return self

    def launch(self, command, **kwargs):
        if self.closed:
            raise RuntimeError('进程会话已经关闭')
        pending = []
        # Defer interruption until the spawned child is registered for cleanup.
        with interrupt_scope(lambda signum, frame: pending.append(signum)):
            kwargs['start_new_session'] = True
            child = subprocess.Popen(command, **kwargs)
            self.children.append(child)
        if pending:
            raise KeyboardInterrupt()
        return child

    def _signal(self, sig):
        for child in self.children:
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                pass

    def _settled(self):
        done = True
        for child in self.children:
            if child.poll() is None:
                done = False
                continue
            # Popen has reaped the direct child. Reap only adopted descendants
            # in our groups, never unrelated children of the embedding program.
            if sys.platform == 'linux':
                while True:
                    try:
                        pid, _ = os.waitpid(-child.pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if pid == 0:
                        break
            try:
                os.killpg(child.pid, 0)
                done = False
            except ProcessLookupError:
                pass
        return done

    def _wait(self, seconds):
        deadline = time.monotonic() + seconds
        while not self._settled():
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return True

    def close(self):
        if self.closed:
            return
        with interrupt_scope(signal.SIG_IGN):
            try:
                self._signal(signal.SIGTERM)
                if not self._wait(self.grace):
                    self._signal(signal.SIGKILL)
                    if not self._wait(self.kill_timeout):
                        raise OSError('强制结束后子进程仍未退出，清理已超时；请检查内核不可中断进程')
            finally:
                self.closed = True
                for child in self.children:
                    for stream in (child.stdin, child.stdout, child.stderr):
                        if stream is not None:
                            stream.close()

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        finally:
            if self.terminal is not None:
                import termios
                try:
                    termios.tcsetattr(self.terminal[0], termios.TCSANOW, self.terminal[1])
                except (OSError, termios.error):
                    pass
            if self.libc is not None and self.old_subreaper is not None:
                self.libc.prctl(36, self.old_subreaper, 0, 0, 0)
            self.signals.__exit__(exc_type, exc, tb)
