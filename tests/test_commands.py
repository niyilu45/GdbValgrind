import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from inc.core import load_report, render_html
from inc.commands import absolute_path


class CommandTests(unittest.TestCase):
    def test_report_exported_by_external_python_script_uses_library_entrypoint(self):
        library = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='external caller ') as directory:
            root = Path(directory)
            caller = root / 'caller'
            work = root / 'working directory'
            output = root / 'reports'
            project = root / 'source project'
            for path in (caller, work, output, project):
                path.mkdir()
            xml = output / 'errors.xml'
            xml.write_text('<valgrindoutput><args><argv><exe>./build/app</exe>'
                           '</argv></args></valgrindoutput>', encoding='utf-8')
            script = caller / 'external_driver.py'
            script.write_text(
                'import sys\n'
                'sys.path.insert(0, ' + repr(str(library)) + ')\n'
                'from inc import export_report\n'
                'export_report("../reports/errors.xml", "../reports/report.html", '
                'project_dir="../source project")\n', encoding='utf-8')
            result = subprocess.run([sys.executable, str(script)], cwd=str(work),
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            html = (output / 'report.html').read_text(encoding='utf-8')
            payload = re.search(r'<script id="report-data" type="application/json">(.*?)</script>',
                                html, re.S)
            self.assertIsNotNone(payload)
            metadata = json.loads(payload.group(1))['debug_command']
            self.assertEqual(metadata['base_args'], [
                str(Path(sys.executable).resolve()), str(library / 'main.py'),
                '--action', 'debug', '--xml', str(xml.resolve()), '--project-dir', str(project.resolve()),
                '--cwd', str(project.resolve())])
            self.assertEqual(metadata['target_args'], [str(project / 'build/app')])
            from main import ArgParser
            target = metadata['target_args'] + ['--mode', 'target-mode', 'two words', "don't", '']
            parsed = ArgParser(metadata['base_args'][2:] + ['--error', 'sample', '--command'] + target)
            self.assertEqual(parsed.command, target)
            self.assertEqual(parsed.xml, xml.resolve())

            # The copied command must remain runnable from an unrelated directory.
            help_result = subprocess.run(metadata['base_args'][:2] + ['--help'],
                                         cwd=str(caller), capture_output=True, timeout=30)
            self.assertEqual(help_result.returncode, 0,
                             help_result.stderr.decode('utf-8', errors='replace'))

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
            for value in args[:2] + [args[5], args[7], args[9]]:
                self.assertTrue(Path(value).is_absolute(), value)
            self.assertEqual(args[6:10], ['--project-dir', str(project), '--cwd', str(project)])
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
