import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from inc import DebugOptions, debug_error, export_report, load_report, render_html, serve_report
from inc import core


ROOT = Path(__file__).resolve().parents[1]


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.report = load_report(ROOT / 'examples/sample.xml', ROOT)

    def test_export_report_returns_data_and_matches_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'report.html'
            report = export_report(ROOT / 'examples/sample.xml', target, project_dir=ROOT)
            self.assertEqual(target.read_text(encoding='utf-8'), render_html(report, source_base=None))
            self.assertEqual(len(report['errors']), 2)
        with self.assertRaises(ValueError):
            export_report(ROOT / 'examples/sample.xml', ROOT / 'examples/sample.xml')

    def test_debug_options_and_argument_forwarding(self):
        command = ['./program', 'argument with spaces']
        options = DebugOptions(command, cwd='/project', auto_values=True, capture_dir='/captures')
        command.append('later mutation')
        with patch.object(core, 'run_debug', return_value=3) as debug:
            result = debug_error(self.report, self.report['errors'][0]['id'], options, frame='0:0')
        self.assertEqual(result, 3)
        error, args = debug.call_args.args
        self.assertEqual(error['id'], self.report['errors'][0]['id'])
        self.assertEqual(args.command, ['./program', 'argument with spaces'])
        self.assertTrue(args.auto_values)
        self.assertEqual(args.project_dir, self.report['project'])
        self.assertEqual(args.frame, '0:0')
        for command in ('./program argument', [], [''], ['app', 'bad\0arg']):
            with self.assertRaises(ValueError):
                DebugOptions(command)
        with self.assertRaises(ValueError):
            debug_error(self.report, 'missing', options)

    def test_browse_does_not_enable_debug(self):
        with patch.object(core, 'serve') as server:
            serve_report(self.report, port=0)
        report, args = server.call_args.args
        self.assertIs(report, self.report)
        self.assertEqual(args.command, [])
        self.assertFalse(args.auto_values)
        self.assertEqual(args.port, 0)
        with self.assertRaises(ValueError):
            serve_report(self.report, port=65536)

    def test_both_entry_points_generate_identical_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            old, example = Path(directory) / 'old.html', Path(directory) / 'example.html'
            commands = [
                [sys.executable, str(ROOT / 'aivalgrind.py'), 'report', str(ROOT / 'examples/sample.xml'), '-p', str(ROOT), '-o', str(old)],
                [sys.executable, str(ROOT / 'main.py'), 'report', '--xml', str(ROOT / 'examples/sample.xml'), '-p', str(ROOT), '-o', str(example)],
            ]
            for command in commands:
                result = subprocess.run(command, cwd=directory, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(old.read_bytes().replace(b'old.html.sources/', b'SOURCES/'), example.read_bytes().replace(b'example.html.sources/', b'SOURCES/'))

    def test_example_calls_public_api(self):
        import main
        with patch.object(main, 'serve_report') as serve:
            main.example_auto_analysis(ROOT / 'examples/sample.xml', ROOT, ['./app'], 'captures', 8765)
        self.assertTrue(serve.call_args.kwargs['options'].auto_values)
        with patch.object(main, 'debug_error', return_value=0) as debug:
            main.example_debug(ROOT / 'examples/sample.xml', ROOT, 'selected-id', ['./app', 'arg'])
        self.assertEqual(debug.call_args.args[1], 'selected-id')


if __name__ == '__main__':
    unittest.main()
