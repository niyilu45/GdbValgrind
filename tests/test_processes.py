import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from inc.processes import ProcessSession, interrupt_scope
from inc import cli, core


class ProcessTests(unittest.TestCase):
    def setUp(self):
        kill_signal = patch.object(signal, 'SIGKILL', getattr(signal, 'SIGKILL', 9), create=True)
        kill_signal.start()
        self.addCleanup(kill_signal.stop)

    def test_term_escalates_and_close_is_idempotent(self):
        session = ProcessSession()
        process = MagicMock(pid=54321)
        session.children.append(process)
        with patch.object(session, '_signal') as kill, patch.object(session, '_wait', side_effect=[False, True]):
            session.close()
            session.close()
        self.assertEqual([call.args[0] for call in kill.call_args_list], [signal.SIGTERM, getattr(signal, 'SIGKILL', 9)])
        process.stdout.close.assert_called_once()

    def test_interrupt_handlers_restored(self):
        original = signal.getsignal(signal.SIGINT)
        with self.assertRaises(KeyboardInterrupt):
            with interrupt_scope():
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        self.assertEqual(signal.getsignal(signal.SIGINT), original)

    def test_spawn_interruption_registers_child_before_raising(self):
        child = MagicMock(pid=12345)
        session = ProcessSession()
        def spawn(*args, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            return child
        with patch('inc.processes.subprocess.Popen', side_effect=spawn):
            with self.assertRaises(KeyboardInterrupt):
                session.launch(['fake'])
        self.assertEqual(session.children, [child])

    def test_cleanup_timeout_is_reported_not_unbounded(self):
        session = ProcessSession()
        with patch.object(session, '_signal'), patch.object(session, '_wait', return_value=False):
            with self.assertRaises(OSError):
                session.close()

    def test_cli_interrupt_returns_130(self):
        with patch.object(cli, 'load_report', side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main(['report', 'input.xml']), 130)

    def test_debug_interrupt_is_not_swallowed(self):
        root = Path(__file__).resolve().parents[1]
        report = core.load_report(root / 'examples/sample.xml', root)
        args = SimpleNamespace(command=[str(root / 'examples/demo.c')], frame=None,
                               cwd=root, project_dir=root, stdin_file=None, auto_values=False,
                               stop_on_error=False)
        processes = MagicMock()
        debugger = MagicMock()
        debugger.wait.side_effect = KeyboardInterrupt()
        processes.launch.side_effect = [MagicMock(pid=54321), debugger]
        with patch.object(core, 'check_debug_environment'), patch.object(core.sys.stdin, 'isatty', return_value=True), patch.object(core.shutil, 'which', side_effect=lambda n: '/usr/bin/'+n), patch.object(core, 'ProcessSession') as manager:
            manager.return_value.__enter__.return_value = processes
            with self.assertRaises(KeyboardInterrupt):
                core.run_debug(report['errors'][0], args)
        processes.close.assert_called_once()
        debugger.wait.assert_called_once_with(timeout=.25)

    def test_server_interrupt_closes_socket(self):
        server = core.DebugServer({'errors': []}, port=0)
        args = SimpleNamespace(command=[], auto_values=False, port=0, open=False)
        with patch.object(core, 'DebugServer', return_value=server), patch.object(server.pending, 'get', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                core.serve({'errors': []}, args)
        self.assertEqual(server.socket.fileno(), -1)


@unittest.skipUnless(sys.platform == 'linux', '需要 Linux 进程组和 /proc')
class LinuxProcessTests(unittest.TestCase):
    def test_interrupt_kills_and_reaps_stubborn_grandchild(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            ids = Path(folder) / 'pids'
            target = ("import os,signal,time; from pathlib import Path; "
                      "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                      "child=os.fork(); "
                      "Path(" + repr(str(ids)) + ").write_text(str(os.getpid())+' '+str(child)) if child else None; "
                      "time.sleep(120)")
            supervisor = ("import sys\nfrom inc.processes import ProcessSession\n"
                          "try:\n with ProcessSession(grace=.2,kill_timeout=2) as s:\n"
                          "  p=s.launch([sys.executable,'-c'," + repr(target) + "])\n"
                          "  p.wait()\nexcept KeyboardInterrupt:\n sys.exit(130)\n")
            compile(supervisor, '<supervisor>', 'exec')
            process = subprocess.Popen([sys.executable, '-c', supervisor], cwd=root,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            owned = []
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if ids.exists() and len(ids.read_text().split()) == 2:
                        owned = list(map(int, ids.read_text().split()))
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(.02)
                self.assertEqual(len(owned), 2)
                process.send_signal(signal.SIGINT)
                _, stderr = process.communicate(timeout=6)
                self.assertEqual(process.returncode, 130, stderr)
                for pid in owned:
                    self.assertFalse(Path('/proc/%s' % pid).exists(), '进程仍存在或未回收: %s' % pid)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                for pid in owned:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
