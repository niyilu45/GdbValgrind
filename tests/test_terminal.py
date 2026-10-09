import io
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
import threading
from unittest.mock import patch

from inc.terminal import TerminalProgress, clip


class TTY(io.StringIO):
    def isatty(self):
        return True


class TerminalTests(unittest.TestCase):
    def test_refresh_continues_without_main_loop_updates(self):
        display = TerminalProgress(Path('.'))
        display.active = True
        display.snapshot = ({}, 'running', '')
        refreshed = threading.Event()
        calls = []
        def render(*args):
            calls.append(args)
            if len(calls) >= 2:
                refreshed.set()
        with patch.object(display, 'render', side_effect=render):
            display.start_updates()
            try:
                self.assertTrue(refreshed.wait(2), 'display must refresh without publish()')
            finally:
                display.stop_updates()
        self.assertIsNone(display.worker)

    def test_q_and_ctrl_c_bytes_interrupt_and_non_tty_does_not_read(self):
        display = TerminalProgress(Path('.'))
        with patch('inc.terminal.select.select') as ready:
            display.check_quit()
            ready.assert_not_called()
        display.keyboard = 81
        for key in (b'q', b'Q', b'\x03'):
            with patch('inc.terminal.select.select', return_value=([81], [], [])), patch('inc.terminal.os.read', return_value=key):
                with self.assertRaises(KeyboardInterrupt):
                    display.check_quit()

    def test_keyboard_mode_enables_signals_and_restores_settings(self):
        settings = [8, 0, 0, 3, 0, 0, [b'X', 1, 0]]
        calls = []
        termios = SimpleNamespace(ICANON=1, ECHO=2, ISIG=4, IXON=8,
            VINTR=0, VMIN=1, VTIME=2, TCSANOW=0, error=OSError,
            tcgetattr=lambda fd: settings, tcsetattr=lambda *args: calls.append(args))
        stdin = SimpleNamespace(isatty=lambda: True, fileno=lambda: 81)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'program.log').touch()
            (root / 'launcher.log').touch()
            with patch.dict('sys.modules', {'termios': termios}), patch('sys.platform', 'linux'), patch('sys.stdin', stdin), patch('sys.stdout', io.StringIO()):
                with TerminalProgress(root):
                    updated = calls[0][2]
                    self.assertEqual(updated[3], 4)
                    self.assertEqual(updated[0], 0)
                    self.assertEqual(updated[6][0], b'\x03')
            self.assertEqual(calls[-1], (81, 0, settings))

    def test_fixed_screen_logs_elapsed_and_restore_on_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'program.log').write_bytes(b'progress 1\n')
            (root / 'launcher.log').write_bytes(b'warning\n')
            screen = TTY()
            summary = {'counts_complete': False, 'kinds': {}, 'locations': 0, 'occurrences': 0}
            with patch('sys.stdout', screen), patch.dict(os.environ, {'TERM': 'xterm'}), patch('inc.terminal.shutil.get_terminal_size', return_value=os.terminal_size((100, 15))), patch('inc.terminal.time.monotonic', return_value=3661):
                with self.assertRaises(KeyboardInterrupt):
                    with TerminalProgress(root, '2026-10-09T10:00:00+08:00', 0) as display:
                        display.render(summary, 'running', 'http://127.0.0.1:8765/')
                        with (root / 'program.log').open('ab') as file:
                            file.write('进度 2\n'.encode('utf-8'))
                        display.render(summary, 'running')
                        self.assertIn('[stdout] 进度 2', display.lines)
                        raise KeyboardInterrupt()
            output = screen.getvalue()
            self.assertIn('01:01:01', output)
            self.assertIn('[stderr] warning', output)
            self.assertEqual(output.count('\x1b[?1049h'), 1)
            self.assertEqual(output.count('\x1b[H'), 2)
            self.assertTrue(output.endswith('\x1b[?25h\x1b[?1049l'))
            self.assertTrue(all(item[0].closed for item in display.streams))

    def test_control_characters_and_wide_text_are_bounded(self):
        self.assertEqual(clip('中文abcdef', 5), '中文a')
        self.assertNotIn('\x1b', clip('\x1b[2J evil\x07', 100))

    def test_large_log_tail_and_non_tty_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'program.log').write_bytes(b'x\n' * 100000 + b'latest\n')
            (root / 'launcher.log').touch()
            screen = io.StringIO()
            with patch('sys.stdout', screen):
                with TerminalProgress(root) as display:
                    display.render({'counts_complete': False, 'kinds': {}, 'locations': 0, 'occurrences': 0}, 'running')
                    self.assertLessEqual(len(display.lines), 300)
                    self.assertEqual(display.lines[-1], '[stdout] latest')
            self.assertEqual(screen.getvalue(), '')
