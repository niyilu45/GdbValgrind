from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from inc import workflow
import xml.etree.ElementTree as ET
from tests.test_aivalgrind import error


class WorkflowTests(unittest.TestCase):
    def test_append_preserves_previous_files_and_report_bytes(self):
        from inc.output import prepare_capture_output, write_manifest
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            original = '<html><head></head><body><p>source and blame</p></body></html>'
            (directory/'full-report.html').write_text(original,encoding='utf-8')
            (directory/'results.sqlite3').write_bytes(b'previous database')
            write_manifest(directory, ['full-report.html','results.sqlite3'])
            old = directory/'captures'/'session-old'
            old.mkdir(parents=True)
            (old/'error-1.txt').write_text('old capture')
            prepare_capture_output(directory)
            self.assertEqual((directory/'results.sqlite3').read_bytes(), b'previous database')
            self.assertEqual((old/'error-1.txt').read_text(), 'old capture')
            workflow.save_combined_report({}, directory, live=True)
            first = (directory/'full-report.html').read_bytes()
            (old/'error-2.txt').write_text('new variable')
            workflow.save_combined_report({}, directory, live=False)
            self.assertEqual((directory/'full-report.html').read_bytes(), first)
            self.assertNotIn(b'new variable',first)
            self.assertIn('new variable',(directory/'capture-updates.js').read_text())

    def run_case(self, *, saved=False, interrupted=False, errors=True, unsupported=False, report_interrupted=False, project=False, pause=False, tty=True, step3=False, same_dir=False):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) if same_dir else Path(root) / 'new-run'
            xml = Path(root) / 'saved.xml'
            report = workflow.core.report_from_root(ET.fromstring('<valgrindoutput>'+ (error('1') if errors else '')+'</valgrindoutput>'), str(xml))
            report['debug_command'] = {'target_args': ['/srv/app', 'arg'],
                      'base_args': ['--cwd', '/srv', '--stdin-file', '/srv/input']}
            (Path(root) / 'demo.c').write_text('int marker;\n' * 30, encoding='utf-8')
            if step3:
                (Path(root) / 'report.html').write_text(workflow.core.render_html(report).replace('</body>', '<p>int marker;</p></body>'), encoding='utf-8')
            def debug(*args):
                if step3 and not same_dir:
                    self.assertFalse((output / 'report.html').exists())
                else:
                    self.assertIn('report-data', (output / 'report.html').read_text(encoding='utf-8'))
                session = output / 'captures' / 'session-test'
                session.mkdir(parents=True)
                (session / 'error-1.txt').write_text('value = <42>', encoding='utf-8')
                if step3:
                    deadline = time.monotonic()+5
                    while time.monotonic()<deadline:
                        live = (output / 'full-report.html').read_text(encoding='utf-8')
                        if 'value =' in (output / 'capture-updates.js').read_text(encoding='utf-8'):
                            break
                        time.sleep(.05)
                    self.assertNotIn('value = &lt;42&gt;', live)
                    self.assertNotIn('http-equiv="refresh"', live)
                    self.assertIn('aivCaptureUpdate', live)
                    self.assertIn('value =', (output / 'capture-updates.js').read_text(encoding='utf-8'))
                return 0
            manager = MagicMock()
            probe = manager.__enter__.return_value.launch.return_value
            probe.communicate.return_value = ('', 'Python scripting is not supported' if unsupported else '')
            probe.returncode = 1 if unsupported else 0
            def collect(*args, **kwargs):
                output.mkdir()
                if interrupted:
                    raise KeyboardInterrupt()
                return 0
            with patch.object(workflow.core, 'check_debug_environment'), patch.object(workflow.sys.stdin, 'isatty', return_value=tty), patch.object(workflow.shutil, 'which', return_value='/usr/bin/gdb'), patch.object(workflow, 'ProcessSession', return_value=manager), patch.object(workflow.core, 'load_report', return_value=report), patch.object(workflow, 'collect_run', side_effect=collect) as first, patch.object(workflow.core, 'save_report', wraps=workflow.core.save_report, side_effect=KeyboardInterrupt() if report_interrupted else None), patch.object(workflow, 'debug_error', side_effect=debug) as replay:
                if interrupted or unsupported or report_interrupted:
                    with self.assertRaises(KeyboardInterrupt if interrupted or report_interrupted else ValueError):
                        workflow.analyze_run(['/srv/app'], output)
                    replay.assert_not_called()
                    if unsupported:
                        first.assert_not_called()
                else:
                    self.assertEqual(workflow.analyze_run([] if saved else ['/srv/app'], output, xml_path=xml if saved else None, project_dir=root if project else None, pause_on_error=pause, step3_only=step3), 0)
                    html = (output / ('full-report.html' if step3 else 'report.html')).read_text(encoding='utf-8')
                    self.assertIn('report-data', html)
                    if project:
                        self.assertIn('int marker;', html)
                    if errors:
                        self.assertIn(report['errors'][0]['id'], html)
                    self.assertFalse((output / 'report.html.tmp').exists())
                    self.assertEqual(first.call_count, 0 if saved else 1)
                    self.assertEqual(replay.call_count, 1 if errors else 0)
                    if errors:
                        combined = (output / 'full-report.html').read_text(encoding='utf-8')
                        self.assertNotIn('value = &lt;42&gt;', combined)
                        self.assertIn('value =', (output / 'capture-updates.js').read_text(encoding='utf-8'))
                        self.assertIn('report-data', combined)
                        self.assertNotIn('http-equiv="refresh"', combined)
                        self.assertEqual(replay.call_args.args[2].auto_continue, not pause)
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

    def test_report_interrupt_never_starts_replay(self): self.run_case(report_interrupted=True)
    def test_report_contains_source_context(self): self.run_case(project=True)

    def test_default_automatic_analysis_without_tty(self): self.run_case(saved=True, tty=False)
    def test_pause_mode_preserves_interactive_debugging(self): self.run_case(pause=True)

    def test_step3_only_merges_capture(self): self.run_case(saved=True, step3=True, project=True)

    def test_step3_same_directory(self): self.run_case(saved=True, step3=True, same_dir=True)
