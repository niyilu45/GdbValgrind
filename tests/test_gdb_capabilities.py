import io
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from inc import core


class GDBCapabilityTests(unittest.TestCase):
    def test_disconnected_capture_ends_supervisor_wait_and_cleans_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'app'
            binary.touch()
            session = root / 'capture'
            session.mkdir()
            script = session / 'capture.py'
            script.touch()
            error = {'id': 'one', 'kind': 'InvalidWrite', 'what': 'Invalid write', 'stacks': []}
            args = SimpleNamespace(frame=None, auto_values=True, command=[str(binary)], cwd=directory,
                project_dir=None, stdin_file=None, capture_dir=directory, stop_on_error=False)
            probe = MagicMock(returncode=0)
            probe.communicate.return_value = ('Python OK', '')
            vg, debugger, processes = MagicMock(pid=123), MagicMock(), MagicMock()
            def fail_wait(**kwargs):
                (session / 'connection-error.txt').write_text('Connection reset by peer', encoding='utf-8')
                raise subprocess.TimeoutExpired('gdb', 0.25)
            debugger.wait.side_effect = fail_wait
            processes.launch.side_effect = [probe, vg, debugger]
            with patch.object(core.sys.stdin, 'isatty', return_value=True), patch.object(core.shutil, 'which', side_effect=lambda n: '/usr/bin/' + n), patch.object(core, 'prepare_capture', return_value=(session, script)), patch('sys.stdout', io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'Connection reset by peer'):
                    core._run_debug(error, args, processes)
            debugger.wait.assert_called_once()
            processes.close.assert_called_once()

    def exercise(self, auto=False, diagnostic='Python scripting is not supported in this copy of GDB.', timeout=False):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'app'
            binary.touch()
            error = {'id': 'first', 'kind': 'InvalidWrite', 'what': 'Invalid write',
                     'stacks': [{'frames': [{'fn': 'main'}]}]}
            args = SimpleNamespace(frame=None, auto_values=auto, navigation_errors=[error],
                command=[str(binary)], cwd=directory, project_dir=None, stdin_file=None,
                capture_dir=directory, stop_on_error=False)
            processes = MagicMock()
            probe = MagicMock(returncode=1)
            probe.communicate.return_value = ('', diagnostic)
            if timeout:
                probe.communicate.side_effect = subprocess.TimeoutExpired('gdb', 5)
            vg = MagicMock(pid=123)
            debugger = MagicMock()
            debugger.wait.return_value = 0
            scripts = []
            def launch(command, **kwargs):
                if '-batch' in command:
                    return probe
                if command[0].endswith('valgrind'):
                    return vg
                scripts.append(Path(command[command.index('-x')+1]).read_text(encoding='utf-8'))
                return debugger
            processes.launch.side_effect = launch
            output = io.StringIO()
            with patch.object(core.sys.stdin, 'isatty', return_value=True), patch.object(core.shutil, 'which', side_effect=lambda n: '/usr/bin/'+n), patch('sys.stdout', output):
                if auto or timeout or diagnostic != 'Python scripting is not supported in this copy of GDB.':
                    with self.assertRaises(ValueError) as failure:
                        core._run_debug(error, args, processes)
                    self.assertEqual(processes.launch.call_count, 1, 'target must not start on probe failure')
                    return str(failure.exception), output.getvalue()
                self.assertEqual(core._run_debug(error, args, processes), 0)
                self.assertIn('break -function "main"', scripts[0])
                self.assertNotIn('python exec', scripts[0])
                self.assertNotIn('navigation.py', scripts[0])
                processes.close.assert_called_once()
                return '', output.getvalue()

    def test_plain_debug_falls_back_without_python(self):
        _, output = self.exercise()
        self.assertIn('已切换为普通断点调试', output)
        self.assertIn('aiv-goto', output)

    def test_auto_values_fails_before_target_starts(self):
        message, _ = self.exercise(auto=True)
        self.assertIn('--auto-values', message)
        self.assertIn('PATH', message)

    def test_timeout_has_bounded_visible_diagnostic(self):
        message, output = self.exercise(timeout=True)
        self.assertIn('超过 5 秒', message)
        self.assertIn('/usr/bin/gdb', output)

    def test_other_python_errors_do_not_silently_fall_back(self):
        message, _ = self.exercise(diagnostic='ImportError: broken GDB installation')
        self.assertIn('ImportError', message)
