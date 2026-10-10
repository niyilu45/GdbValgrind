from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from inc.blame import source_blame, _cache
from inc.core import Sources


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
            with patch('inc.blame.shutil.which', return_value='/git'), patch('inc.blame.run_git', side_effect=failure):
                self.assertEqual(source_blame(self.path, 1, 1), {})
        with patch('inc.blame.shutil.which', return_value='/git'), patch('inc.blame.run_git', return_value=SimpleNamespace(returncode=128, stdout='')):
            self.assertEqual(source_blame(self.path, 1, 1), {})

    def test_line_metadata_and_uncommitted_changes(self):
        output = 'a'*40 + ' 1 1 1\nauthor Alice\nauthor-time 0\nsummary first change\n\tint x;\n' + '0'*40 + ' 2 2 1\nauthor Not Committed Yet\n\tint y;\n'
        with patch('inc.blame.shutil.which', return_value='/git'), patch('inc.blame.run_git', return_value=SimpleNamespace(returncode=0, stdout=output)) as run:
            result = source_blame(self.path, 1, 2)
            self.assertEqual(result[1]['author'], 'Alice')
            self.assertEqual(result[1]['summary'], 'first change')
            self.assertEqual(result[2]['commit'], '')
            self.assertEqual(source_blame(self.path, 1, 2), result)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(source_blame(self.path, 2, 2), {2: result[2]})
            self.assertEqual(run.call_count, 1)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index('-L') + 1], '1,2')

    def test_files_have_independent_budgets_and_frames_share_results(self):
        other = self.path.with_name('other.c')
        other.write_text('int y;\n')
        sources = Sources(self.path.parent)
        first = {'file': self.path.name, 'line': '1'}
        second = {'file': other.name, 'line': '1'}
        with patch('inc.core.source_blame', side_effect=[{}, {1: {'text': 'int y;', 'author': 'Alice'}}]) as blame:
            sources.enrich(first)
            sources.enrich(dict(first))
            sources.enrich(second)
            self.assertEqual(blame.call_count, 2)
            self.assertEqual(second['source'][0]['blame']['author'], 'Alice')

    def test_failed_query_is_retried_next_time(self):
        good = 'a'*40 + ' 1 1 1\nauthor Alice\n\tint x;\n'
        with patch('inc.blame.shutil.which', return_value='/git'), patch('inc.blame.run_git', side_effect=[SimpleNamespace(returncode=128, stdout=''), SimpleNamespace(returncode=0, stdout=good)]) as run:
            self.assertEqual(source_blame(self.path, 1, 1), {})
            self.assertIn(1, source_blame(self.path, 1, 1))
            self.assertEqual(run.call_count, 2)

    def test_stack_queries_only_visible_ranges(self):
        self.path.write_text('\n'.join('line %d' % n for n in range(1, 101)))
        sources = Sources(self.path.parent)
        with patch('inc.core.source_blame', return_value={}) as blame:
            for line in (15, 75, 15):
                sources.enrich({'file': self.path.name, 'line': str(line)})
            self.assertEqual([call.args[1:] for call in blame.call_args_list], [(5, 25), (65, 85)])

    def test_progress_can_cancel_before_blame(self):
        stages = []
        def progress(text):
            stages.append(text)
            if 'git blame' in text:
                raise KeyboardInterrupt()
        sources = Sources(self.path.parent, progress=progress)
        with patch('inc.core.source_blame') as blame:
            with self.assertRaises(KeyboardInterrupt):
                sources.enrich({'file': self.path.name, 'line': '1'})
            blame.assert_not_called()
        self.assertTrue(any(str(self.path) in stage for stage in stages))
