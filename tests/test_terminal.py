import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inc.terminal import TerminalProgress, clip


class TTY(io.StringIO):
    def isatty(self):
        return True


class TerminalTests(unittest.TestCase):
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
