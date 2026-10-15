import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from main import ArgParser
from inc.runtime_paths import configure_paths


class CompactPathsTests(unittest.TestCase):
    def test_five_scenarios_use_fixed_files(self):
        for mode in ('fast', 'full'):
            for action, flags in [('collect', ['--report']), ('report', []), ('analyze', []),
                                  ('analyze', ['--step3-only']), ('analyze', ['--from-xml'])]:
                args = ArgParser(['--action', action, '--mode', mode, '--retPath', 'results'] + flags)
                if action == 'report':
                    self.assertEqual(args.xml, Path('results/errors.xml'))
                    self.assertEqual(args.output, Path('results/report.html'))
                elif action == 'analyze':
                    self.assertEqual(args.xml, Path('results/errors.xml') if flags else None)
                    if flags == ['--step3-only']:
                        self.assertEqual(args.base_report, Path('results/report.html'))

    def test_irrelevant_options_warn_and_target_options_are_preserved(self):
        with patch('sys.stderr', new_callable=io.StringIO) as stderr:
            args = ArgParser(['--action', 'summary', '--retPath', 'results', '--mode', 'full',
                              '--workers=8', '--pause-on-error', '--progPath', '/app', '--weird', 'value'])
        self.assertEqual(args.command, [])
        for option in ('--mode', '--workers', '--pause-on-error', '--progPath'):
            self.assertIn(option, stderr.getvalue())
        args = ArgParser(['--action', 'collect', '--retPath', 'results', '--progPath', './app', '--retPath', 'target', '--mode', 'other'])
        self.assertEqual(args.command, ['./app', '--retPath', 'target', '--mode', 'other'])
        with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
            ArgParser(['--action', 'summary', '--retPath', 'results', '--misspelled', 'x'])

    def test_program_parent_and_git_root_and_saved_workdir(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.git').mkdir()
            binary = root / 'build' / 'my app'
            binary.parent.mkdir()
            binary.write_bytes(b'placeholder')
            output = root / 'results'
            output.mkdir()
            args = ArgParser(['--action', 'analyze', '--retPath', str(output), '--progPath', str(binary), 'two words'])
            configure_paths(args)
            self.assertEqual(args.cwd, binary.parent)
            self.assertEqual(args.project_dir, root)
            self.assertEqual(args.command, [str(binary), 'two words'])
            (output / 'run.json').write_text(json.dumps({'schema':1, 'xml_file':'errors.xml',
                'cwd':str(root), 'command':[str(binary), '--input', 'input.dat']}), encoding='utf-8')
            args = ArgParser(['--action', 'analyze', '--from-xml', '--retPath', str(output)])
            configure_paths(args)
            self.assertEqual(args.cwd, root)
            self.assertEqual(args.command, [str(binary), '--input', 'input.dat'])

    def test_report_uses_program_only_for_source_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'app'
            binary.touch()
            args = ArgParser(['--action', 'report', '--retPath', folder, '--progPath', str(binary)])
            configure_paths(args)
            self.assertEqual(args.project_dir, Path(folder))
            self.assertEqual(args.output, Path(folder) / 'report.html')
