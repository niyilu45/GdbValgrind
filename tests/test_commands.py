import json
from pathlib import Path
import tempfile
import unittest

from inc.core import load_report, render_html
from inc.commands import absolute_path


class CommandTests(unittest.TestCase):
    def test_xml_arguments_and_project_survive_partial_report(self):
        with tempfile.TemporaryDirectory(prefix='report space ') as directory:
            project = Path(directory) / 'project space'
            project.mkdir()
            xml = Path(directory) / 'errors.xml'
            xml.write_text('<valgrindoutput><args><argv><exe>./build/my app</exe>'
                           '<arg>a b</arg><arg>don\'t</arg><arg></arg></argv></args><error>')
            report = load_report(xml, project)
            meta = report['debug_command']
            self.assertTrue(meta['ready'])
            self.assertFalse(report['xml_complete'])
            self.assertEqual(meta['target_args'], [str(project / 'build/my app'), 'a b', "don't", ''])
            args = meta['base_args']
            for value in args[:2] + [args[3], args[5], args[7]]:
                self.assertTrue(Path(value).is_absolute(), value)
            self.assertEqual(args[4:8], ['--project-dir', str(project), '--cwd', str(project)])
            self.assertNotIn('./your_program', render_html(report))

    def test_collection_metadata_preserves_workdir_and_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            xml = Path(directory) / 'errors.xml'
            xml.write_text('<valgrindoutput/>')
            saved = {'schema': 1, 'xml_file': 'errors.xml', 'command': ['./app', 'input.txt'],
                     'cwd': '/srv/my project', 'stdin_file': 'stdin.txt'}
            (xml.parent / 'run.json').write_text(json.dumps(saved))
            meta = load_report(xml, directory)['debug_command']
            self.assertEqual(meta['source'], 'collection')
            self.assertEqual(meta['target_args'], ['/srv/my project/app', 'input.txt'])
            self.assertEqual(meta['base_args'][-4:], ['--cwd', '/srv/my project', '--stdin-file', '/srv/my project/stdin.txt'])

    def test_invalid_or_unrelated_metadata_does_not_break_report(self):
        with tempfile.TemporaryDirectory() as directory:
            xml = Path(directory) / 'errors.xml'
            xml.write_text('<valgrindoutput/>')
            for saved in ['{', 'null', '[]', '{"schema":1,"xml_file":"other.xml"}']:
                with self.subTest(saved=saved):
                    (xml.parent / 'run.json').write_text(saved)
                    self.assertFalse(load_report(xml)['debug_command']['ready'])

    def test_foreign_absolute_paths_not_rebased(self):
        self.assertEqual(absolute_path('/srv/app', 'D:/project'), '/srv/app')
        self.assertEqual(absolute_path('C:\\project\\app', '/srv'), 'C:\\project\\app')
