import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from main import ArgParser, AiValgrind
from inc.api import DebugOptions, _arguments
from inc.core import prepare_capture


class ModeTests(unittest.TestCase):
    def test_named_arguments_preserve_all_target_arguments(self):
        target = ['./my app', '--mode', 'production', '--xml', 'input.xml',
                  '--action', 'target-action', '--command', 'nested', '--',
                  'two words', "don't", '', 'literal;$HOME']
        args = ArgParser(['--action', 'analyze', '--mode', 'fast',
                          '--output-dir', 'results', '--command'] + target)
        self.assertEqual(args.command, target)
        self.assertEqual(args.mode, 'fast')
        with patch('main.print_tool_versions', return_value={}), patch('main.analyze_run', return_value=0) as run:
            self.assertEqual(AiValgrind(args), 0)
        self.assertEqual(run.call_args.args[0], target)
        self.assertEqual(ArgParser(['--action=report', '--xml', 'errors.xml']).xml, Path('errors.xml'))

    def test_named_command_requires_executable(self):
        import io
        with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
            ArgParser(['--action', 'analyze', '--output-dir', 'results', '--command'])

    def test_entry_functions_are_implemented_in_main(self):
        import inspect
        import main
        for function in (main.ArgParser, main.AiValgrind):
            self.assertEqual(Path(inspect.getsourcefile(function)).resolve(),
                             Path(main.__file__).resolve())

    def test_parser_and_dispatch_forward_modes_and_target_arguments(self):
        for mode in ('fast', 'full'):
            args = ArgParser(['analyze', '--output-dir', 'results', '--mode', mode,
                              '--', './app', '--mode', 'target-option'])
            with patch('main.print_tool_versions', return_value={}), patch('main.analyze_run', return_value=0) as run:
                self.assertEqual(AiValgrind(args), 0)
            self.assertEqual(run.call_args.kwargs['mode'], mode)
            self.assertEqual(run.call_args.args[0], ['./app', '--mode', 'target-option'])
        self.assertEqual(ArgParser(['analyze', '--output-dir', 'results']).mode, 'full')

    def test_library_mode_reaches_embedded_capture_without_changing_precision(self):
        precision = {'options': ['--vgdb=full'], 'version': 'valgrind-3.22.0'}
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('fast', 'full'):
                args = _arguments(DebugOptions(['./app'], mode=mode), None)
                _, script = prepare_capture(directory, 'error', mode=args.mode, precision=precision)
                scope = {}
                exec(script.read_text(encoding='utf-8').splitlines()[0], scope)
                self.assertEqual(scope['_capture_config']['mode'], mode)
                self.assertEqual(scope['_capture_config']['precision'], precision)
        with self.assertRaises(ValueError):
            DebugOptions(['./app'], mode='invalid')

    def test_legacy_aliases_and_external_entry(self):
        args = ArgParser(['auto-values', '--xml', 'errors.xml', '--mode', 'fast', '--', './app'])
        self.assertEqual(args.action, 'serve')
        self.assertEqual(args.xml, Path('errors.xml'))
        self.assertTrue(args.auto_values)
        self.assertEqual(args.mode, 'fast')
        import main
        with patch.object(main, 'AiValgrind', return_value=7) as run:
            self.assertEqual(main.main(['summary', 'errors.xml']), 7)
        self.assertEqual(run.call_args.args[0].action, 'summary')
