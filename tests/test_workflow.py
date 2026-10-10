from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from inc import workflow


class WorkflowTests(unittest.TestCase):
    def run_case(self, *, saved=False, interrupted=False, errors=True, unsupported=False):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'new-run'
            xml = Path(root) / 'saved.xml'
            report = {'errors': [{'id': 'first'}] if errors else [],
                      'debug_command': {'target_args': ['/srv/app', 'arg'],
                      'base_args': ['--cwd', '/srv', '--stdin-file', '/srv/input']}}
            manager = MagicMock()
            probe = manager.__enter__.return_value.launch.return_value
            probe.communicate.return_value = ('', 'Python scripting is not supported' if unsupported else '')
            probe.returncode = 1 if unsupported else 0
            def collect(*args, **kwargs):
                output.mkdir()
                if interrupted:
                    raise KeyboardInterrupt()
                return 0
            with patch.object(workflow.core, 'check_debug_environment'), patch.object(workflow.sys.stdin, 'isatty', return_value=True), patch.object(workflow.shutil, 'which', return_value='/usr/bin/gdb'), patch.object(workflow, 'ProcessSession', return_value=manager), patch.object(workflow.core, 'load_report', return_value=report), patch.object(workflow, 'collect_run', side_effect=collect) as first, patch.object(workflow, 'export_report', side_effect=AssertionError('analysis must not export HTML')), patch.object(workflow, 'debug_error', return_value=0) as replay:
                if interrupted or unsupported:
                    with self.assertRaises(KeyboardInterrupt if interrupted else ValueError):
                        workflow.analyze_run(['/srv/app'], output)
                    replay.assert_not_called()
                    if unsupported:
                        first.assert_not_called()
                else:
                    self.assertEqual(workflow.analyze_run([] if saved else ['/srv/app'], output, xml_path=xml if saved else None), 0)
                    self.assertEqual(first.call_count, 0 if saved else 1)
                    self.assertEqual(replay.call_count, 1 if errors else 0)
                    if saved and errors:
                        options = replay.call_args.args[2]
                        self.assertEqual(options.command, ('/srv/app', 'arg'))
                        self.assertEqual(options.cwd, '/srv')
                        self.assertEqual(options.stdin_file, '/srv/input')
                        self.assertTrue(options.auto_values)

    def test_single_command(self): self.run_case()
    def test_saved_report_reuses_command(self): self.run_case(saved=True)
    def test_interrupt_never_restarts_target(self): self.run_case(interrupted=True)
    def test_no_errors_skips_replay(self): self.run_case(errors=False)
    def test_unsupported_gdb_fails_before_collection(self): self.run_case(unsupported=True)
