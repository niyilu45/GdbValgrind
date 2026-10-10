import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from inc.gitprocess import run_git


class GitProcessTests(unittest.TestCase):
    def test_real_process_timeout_is_bounded_and_reaped(self):
        real_popen = subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        with patch('inc.gitprocess.subprocess.Popen', side_effect=launch):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_git([sys.executable, '-c', 'import time; time.sleep(60)'], 0.2)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNotNone(children[0].returncode)

    def test_cancel_during_wait_reaps_child(self):
        children = []
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        def cancel():
            if children:
                raise KeyboardInterrupt()
        with patch('inc.gitprocess.subprocess.Popen', side_effect=launch):
            with self.assertRaises(KeyboardInterrupt):
                run_git([sys.executable, '-c', 'import time; time.sleep(60)'], 5, cancel)
        self.assertIsNotNone(children[0].returncode)

    def test_output_preserved(self):
        result = run_git([sys.executable, '-c', 'import sys; print("ok"); print("warning", file=sys.stderr)'], 5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'ok')
        self.assertEqual(result.stderr.strip(), 'warning')
