import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main
from inc.workflow import resolve_report_xml


class ScenarioTests(unittest.TestCase):
    def test_collect_and_export_runs_target_only_once_without_gdb(self):
        for mode in ('fast', 'full'):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'errors.xml').write_text('<valgrindoutput/>', encoding='utf-8')
                args = main.ArgParser(['collect', '--report', '--mode', mode,
                                      '--output-dir', folder, '--', './app'])
                with patch.object(main, 'collect_run', return_value=7) as collect, patch.object(main, 'run_debug') as debug, patch.object(main, 'print_tool_versions', return_value={}):
                    self.assertEqual(main.AiValgrind(args), 7)
                collect.assert_called_once()
                debug.assert_not_called()
                self.assertIn('report-data', (root / 'report.html').read_text(encoding='utf-8'))

    def test_xml_report_modes_produce_same_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            xml = root / 'errors.xml'
            xml.write_text('<valgrindoutput/>', encoding='utf-8')
            for mode in ('fast', 'full'):
                with patch.object(main, 'print_tool_versions', return_value={}):
                    self.assertEqual(main.main(['report', str(xml), '--mode', mode, '-o', str(root / (mode + '.html'))]), 0)
            self.assertEqual((root / 'fast.html').read_bytes(), (root / 'full.html').read_bytes())

    def test_html_resolves_relocated_xml_without_executing_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            xml = root / 'errors.xml'
            xml.write_text('<valgrindoutput/>', encoding='utf-8')
            html = root / 'report.html'
            html.write_text('<script id="report-data" type="application/json">' + json.dumps({'xml':'/old/location/errors.xml'}) + '</script>', encoding='utf-8')
            self.assertEqual(resolve_report_xml(html), xml.resolve())
            xml.unlink()
            with self.assertRaisesRegex(ValueError, 'XML'):
                resolve_report_xml(html)
            html.write_text('<html>no payload</html>', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'XML'):
                resolve_report_xml(html)

    def test_html_only_entry_forwards_mode_and_base_report(self):
        for mode in ('fast', 'full'):
            with patch.object(main, 'analyze_run', return_value=0) as run, patch.object(main, 'print_tool_versions', return_value={}):
                self.assertEqual(main.main(['analyze', '--step3-only', '--mode', mode,
                                           '--base-report', 'report.html', '--output-dir', 'results', '--', './app']), 0)
            self.assertTrue(run.call_args.kwargs['step3_only'])
            self.assertEqual(run.call_args.kwargs['base_report'], Path('report.html'))
            self.assertEqual(run.call_args.kwargs['mode'], mode)
            self.assertIsNone(run.call_args.kwargs['xml_path'])
