from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from inc.blame import source_blame, _cache


class BlameTests(unittest.TestCase):
    def setUp(self):
        _cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'file.c'
        self.path.write_text('int x;\n')

    def test_unsupported_git_and_timeout_are_nonfatal(self):
        with patch('inc.blame.shutil.which', return_value=None):
            self.assertEqual(source_blame(self.path, 1, 1), {})
        for failure in (OSError('missing'), subprocess.TimeoutExpired('git', 2)):
            with patch('inc.blame.shutil.which', return_value='/git'), patch('inc.blame.subprocess.run', side_effect=failure):
                self.assertEqual(source_blame(self.path, 1, 1), {})
        with patch('inc.blame.shutil.which', return_value='/git'), patch('inc.blame.subprocess.run', return_value=SimpleNamespace(returncode=128, stdout='')):
            self.assertEqual(source_blame(self.path, 1, 1), {})

    def test_line_metadata_and_uncommitted_changes(self):
        output = 'a'*40 + ' 1 1 1\nauthor Alice\nauthor-time 0\nsummary first change\n\tint x;\n' + '0'*40 + ' 2 2 1\nauthor Not Committed Yet\n\tint y;\n'
        with patch('inc.blame.shutil.which', return_value='/git'), patch('inc.blame.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=output)) as run:
            result = source_blame(self.path, 1, 2)
            self.assertEqual(result[1]['author'], 'Alice')
            self.assertEqual(result[1]['summary'], 'first change')
            self.assertEqual(result[2]['commit'], '')
            self.assertEqual(source_blame(self.path, 1, 2), result)
            self.assertEqual(run.call_count, 1)
