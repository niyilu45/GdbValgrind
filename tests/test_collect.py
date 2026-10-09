import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from inc import core, cli, export_report
from inc.xmlstream import XMLStream
from inc import collect as collection


ERROR = '<error><unique>0x1</unique><kind>InvalidWrite</kind><what>Invalid write of size 4</what><stack><frame><fn>bad</fn><file>main.c</file><line>9</line></frame></stack></error>'
PREFIX = '<valgrindoutput><tool>memcheck</tool>' + ERROR


class PartialXMLTests(unittest.TestCase):
    def test_partial_error_discarded_and_ids_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'errors.xml'
            path.write_text(PREFIX + '<error><kind>InvalidRead</kind><stack>')
            partial = core.load_report(path)
            self.assertEqual(len(partial['errors']), 1)
            self.assertFalse(partial['finished'])
            self.assertFalse(partial['counts_complete'])
            self.assertFalse(partial['xml_complete'])
            html_path = Path(folder) / 'partial.html'
            export_report(path, html_path)
            self.assertIn('"xml_complete": false', html_path.read_text(encoding='utf-8'))
            with patch.object(cli, 'run_debug', return_value=0) as debug:
                self.assertEqual(cli.main(['debug', str(path), '--error', partial['errors'][0]['id'], '--auto-values', '--', './app']), 0)
            self.assertTrue(debug.call_args.args[1].auto_values)
            self.assertEqual(debug.call_args.args[0]['id'], partial['errors'][0]['id'])
            with self.assertRaises(ValueError):
                core.load_report(path, allow_partial=False)
            path.write_text(PREFIX + '<errorcounts><pair><unique>0x1</unique><count>30</count></pair></errorcounts><status><state>FINISHED</state></status></valgrindoutput>')
            full = core.load_report(path)
            self.assertEqual(full['errors'][0]['id'], partial['errors'][0]['id'])
            self.assertEqual(full['occurrences'], 30)
            self.assertTrue(full['counts_complete'])
            self.assertEqual(core.pick_frame(partial['errors'][0])['line'], '9')

    def test_truncated_counts_preserve_complete_pairs(self):
        stream = XMLStream()
        raw = (PREFIX + '<errorcounts><pair><unique>0x1</unique><count>12</count></pair><pair>').encode()
        for byte in raw:
            stream.feed(bytes([byte]))
        stream.finish()
        report = core.report_from_root(stream.root, 'test.xml', xml_complete=False)
        self.assertEqual(report['occurrences'], 12)
        self.assertFalse(report['counts_complete'])

    def test_split_utf8_and_incomplete_character(self):
        stream = XMLStream()
        data = (PREFIX + '<error><what>中文').encode('utf-8')
        for byte in data[:-1]:
            stream.feed(bytes([byte]))
        stream.finish()
        self.assertEqual(len(stream.root.findall('error')), 1)

    def test_dtd_cannot_cross_chunk_boundary(self):
        stream = XMLStream()
        stream.feed(b'<!DOC')
        with self.assertRaises(ValueError):
            stream.feed(b'TYPE valgrindoutput>')


class CollectionTests(unittest.TestCase):
    def test_interrupt_preserves_raw_and_atomic_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'run'
            executable = Path(folder) / 'app'
            executable.touch()
            process = MagicMock()
            def interrupt():
                (directory / 'errors.xml').write_text(PREFIX + '<error>')
                raise KeyboardInterrupt()
            process.poll.side_effect = interrupt
            manager = MagicMock()
            manager.__enter__.return_value.launch.return_value = process
            with patch.object(collection.sys, 'platform', 'linux'), patch.object(collection.shutil, 'which', return_value='/usr/bin/valgrind'), patch.object(collection, 'ProcessSession', return_value=manager):
                with self.assertRaises(KeyboardInterrupt):
                    collection.collect_run([str(executable)], directory, live_port=0)
            status = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['state'], 'interrupted')
            self.assertIn('started_at', status)
            self.assertGreaterEqual(status['elapsed_seconds'], 0)
            saved = (directory / 'report.html').read_text(encoding='utf-8')
            self.assertIn('InvalidWrite', saved)
            self.assertNotIn('"live": true', saved)
            self.assertEqual(status['kinds']['InvalidWrite']['locations'], 1)
            self.assertFalse(status['counts_complete'])
            self.assertEqual(len(core.load_report(directory / 'errors.xml')['errors']), 1)
            manager.__exit__.assert_called_once()
            self.assertFalse((directory / 'status.json.tmp').exists())
            metadata = core.load_report(directory / 'errors.xml')['debug_command']
            self.assertEqual(metadata['source'], 'collection')
            self.assertEqual(metadata['target_args'], [str(executable.resolve())])

    def test_completed_collection_drains_final_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'run'
            executable = Path(folder) / 'app'
            executable.touch()
            process = MagicMock()
            def finish():
                (directory / 'errors.xml').write_text(PREFIX + '<errorcounts><pair><unique>0x1</unique><count>9</count></pair></errorcounts><status><state>FINISHED</state></status></valgrindoutput>')
                return 0
            process.poll.side_effect = finish
            manager = MagicMock()
            manager.__enter__.return_value.launch.return_value = process
            with patch.object(collection.sys, 'platform', 'linux'), patch.object(collection.shutil, 'which', return_value='/usr/bin/valgrind'), patch.object(collection, 'ProcessSession', return_value=manager):
                self.assertEqual(collection.collect_run([str(executable)], directory), 0)
                with self.assertRaises(FileExistsError):
                    collection.collect_run([str(executable)], directory)
            status = json.loads((directory / 'status.json').read_text())
            self.assertEqual(status['occurrences'], 9)
            self.assertTrue(status['counts_complete'])
