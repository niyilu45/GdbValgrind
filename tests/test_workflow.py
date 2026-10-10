from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from inc import workflow
import xml.etree.ElementTree as ET
from tests.test_aivalgrind import error


class WorkflowTests(unittest.TestCase):
    def test_step_three_rejects_unrelated_html_before_launch(self):
        xml=Path(__file__).resolve().parents[1]/'examples/sample.xml'
        with tempfile.TemporaryDirectory() as root:
            base=Path(root)/'report.html'
            base.write_text('<script id="report-data" type="application/json">{"errors":[]}</script>',encoding='utf-8')
            with patch.object(workflow.core,'check_debug_environment') as probe:
                with self.assertRaisesRegex(ValueError,'不一致'):
                    workflow.analyze_run(['app'],root,xml_path=xml,step3_only=True,base_report=base)
                probe.assert_not_called()

    def test_new_step_two_report_replaces_old_combined_report(self):
        import json
        def html(identity):
            return '<html><head></head><body><script id="report-data" type="application/json">'+json.dumps({'errors':[{'id':identity,'kind':'InvalidRead'}]})+'</script></body></html>'
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            workflow.save_combined_report({},directory,base_html=html('old'),replay_id='first')
            workflow.save_combined_report({},directory,base_html=html('new'),replay_id='second')
            result=(directory/'full-report.html').read_text(encoding='utf-8')
            self.assertEqual(workflow.report_payload(result)['errors'][0]['id'],'new')
            self.assertIn(workflow.report_key(html('new')),result)
            sidecar=(directory/'capture-updates.js').read_text(encoding='utf-8')
            self.assertIn(workflow.report_key(html('new')),sidecar)
            self.assertIn('second',sidecar)
            before=(directory/'full-report.html').stat().st_mtime_ns
            workflow.save_combined_report({},directory,base_html=html('new'),replay_id='second')
            self.assertEqual((directory/'full-report.html').stat().st_mtime_ns,before)

    def test_older_workflow_does_not_break_cli_import(self):
        import importlib
        import io
        from inc import cli
        refresh = workflow.refresh_capture_report
        try:
            del workflow.refresh_capture_report
            importlib.reload(cli)
            with patch('sys.stdout', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as result:
                    cli.main(['--help'])
            self.assertEqual(result.exception.code, 0)
            with patch('sys.stderr', new_callable=io.StringIO) as stderr:
                self.assertEqual(cli.main(['refresh-captures', '--output-dir', '.']), 2)
            self.assertIn(str(workflow.__file__), stderr.getvalue())
            self.assertIn('refresh_capture_report', stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())
        finally:
            workflow.refresh_capture_report = refresh

    def test_refresh_upgrades_viewer_without_changing_captured_data(self):
        from inc.cli import main
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            old = '<html><body><p>source</p><section id="capture-results">old</section><script>// aiv-stack-captures-v4</script></body></html>'
            (directory/'full-report.html').write_text(old, encoding='utf-8')
            sidecar = b'window.aivCaptureUpdate({live:false,items:[]});'
            (directory/'capture-updates.js').write_bytes(sidecar)
            with patch.object(workflow, 'debug_error') as debug:
                self.assertEqual(main(['refresh-captures', '--output-dir', root]), 0)
                debug.assert_not_called()
            html = (directory/'full-report.html').read_text(encoding='utf-8')
            self.assertIn('aiv-stack-captures-v19', html)
            self.assertNotIn('aiv-stack-captures-v4', html)
            self.assertIn('<p>source</p>', html)
            self.assertEqual(html.count('id="capture-results"'), 1)
            self.assertEqual((directory/'capture-updates.js').read_bytes(), sidecar)
            workflow.refresh_capture_report(directory)
            self.assertEqual((directory/'full-report.html').read_text(encoding='utf-8'), html)

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
