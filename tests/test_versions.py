import io
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from inc.versions import print_tool_versions


class VersionTests(unittest.TestCase):
    def test_report_does_not_probe_debug_tools(self):
        with patch('sys.stdout', io.StringIO()) as out, patch('inc.versions.shutil.which') as which:
            print_tool_versions('report')
            self.assertIn('Python:', out.getvalue())
            which.assert_not_called()

    def test_collect_only_probes_valgrind_and_debug_probes_all(self):
        for feature, expected in [('collect', ['valgrind']), ('analyze', ['valgrind', 'gdb', 'vgdb'])]:
            with patch('sys.stdout', io.StringIO()) as out, patch('inc.versions.shutil.which', side_effect=lambda n: '/usr/bin/' + n), patch('inc.versions.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='version 1\ncopyright')) as run:
                print_tool_versions(feature)
                self.assertEqual([call.args[0][0].split('/')[-1] for call in run.call_args_list], expected)
                self.assertTrue(all(call.kwargs['timeout'] == 3 for call in run.call_args_list))
                self.assertIn('version 1', out.getvalue())

    def test_timeout_and_missing_tool_are_explicit(self):
        with patch('sys.stdout', io.StringIO()) as out, patch('inc.versions.shutil.which', side_effect=['/vg', None, '/vgdb']), patch('inc.versions.subprocess.run', side_effect=[subprocess.TimeoutExpired('vg', 3), SimpleNamespace(returncode=1, stdout='unknown option')]):
            print_tool_versions('debug')
            self.assertIn('版本查询超时', out.getvalue())
            self.assertIn('gdb: 未找到', out.getvalue())
            self.assertIn('版本查询失败', out.getvalue())
