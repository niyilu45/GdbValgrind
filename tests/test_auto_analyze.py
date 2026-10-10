import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from inc import cli, core, DebugOptions

ROOT = Path(__file__).resolve().parents[1]


class AutomaticAnalyzeTests(unittest.TestCase):
    def test_both_entry_points_forward_pause_option(self):
        import main
        for entry in (cli.main, main.main):
            for pause in (False, True):
                with self.subTest(entry=entry, pause=pause):
                    with patch.object(cli, 'analyze_run', return_value=0) as run, patch.object(cli, 'print_tool_versions'):
                        args = ['analyze', '--output-dir', 'results']
                        if pause:
                            args.append('--pause-on-error')
                        self.assertEqual(entry(args + ['--', '/srv/app']), 0)
                        self.assertEqual(run.call_args.kwargs['pause_on_error'], pause)

    def test_auto_continue_requires_capture(self):
        with self.assertRaises(ValueError):
            DebugOptions(['/app'], auto_continue=True)

    def test_automatic_gdb_launch_is_batch_without_navigation_or_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'app'
            binary.touch()
            error = {'id': 'one', 'kind': 'InvalidWrite', 'what': 'Invalid write', 'stacks': []}
            args = SimpleNamespace(frame=None, auto_values=True, auto_continue=True,
                command=[str(binary)], cwd=directory, project_dir=None, stdin_file=None,
                capture_dir=directory, stop_on_error=False, navigation_errors=[error])
            processes = MagicMock()
            probe = MagicMock(returncode=0)
            probe.communicate.return_value = ('Python OK', '')
            target = MagicMock(pid=123)
            debugger = MagicMock()
            debugger.wait.return_value = 0
            launches = []
            def launch(command, **kwargs):
                if '-x' in command:
                    launches.append((command, Path(command[command.index('-x')+1]).read_text(encoding='utf-8')))
                    return debugger
                if command[0].endswith('valgrind'):
                    return target
                return probe
            processes.launch.side_effect = launch
            with patch.object(core.sys.stdin, 'isatty', return_value=False), patch.object(core.shutil, 'which', side_effect=lambda n: '/usr/bin/' + n), patch('sys.stdout', io.StringIO()):
                self.assertEqual(core._run_debug(error, args, processes), 0)
            command, script = launches[0]
            self.assertIn('-batch', command)
            self.assertIn('-return-child-result', command)
            self.assertIn('run_to_completion()', script)
            self.assertNotIn('navigation.py', script)
            processes.close.assert_called_once()


@unittest.skipUnless(sys.platform == 'linux' and all(shutil.which(n) for n in ('cc', 'valgrind', 'vgdb', 'gdb')), '需要 Linux 和 GDB/Valgrind')
class AutomaticAnalyzeIntegrationTests(unittest.TestCase):
    def test_two_pass_analysis_finishes_without_input_and_captures_later_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, marker = root / 'demo.c', root / 'demo', root / 'finished'
            source.write_text(r'''#include <stdlib.h>
#include <stdio.h>
__attribute__((noinline)) void first(void) {
    volatile int *p = malloc(sizeof(int));
    p[1] = 1;
    free((void *)p);
}
__attribute__((noinline)) void second(void) {
    volatile int *p = malloc(sizeof(int));
    p[2] = 2;
    free((void *)p);
}
int main(int argc, char **argv) {
    for (int i = 0; i < 3; i++) first();
    second();
    FILE *f = fopen(argv[1], "a");
    if (!f) return 2;
    fputs("finished\n", f);
    fclose(f);
    return 0;
}
''', encoding='utf-8')
            subprocess.run(['cc', '-g', '-O0', str(source), '-o', str(binary)], check=True, capture_output=True)
            output = root / 'run'
            run = subprocess.run([sys.executable, str(ROOT / 'aivalgrind.py'), 'analyze', '--no-web',
                '--plain-terminal', '--output-mode', 'file', '--output-dir', str(output), '--', str(binary), str(marker)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(marker.read_text().splitlines(), ['finished', 'finished'])
            snapshots = list((output / 'captures').glob('session-*/error-*.json'))
            self.assertEqual(len(snapshots), 2, run.stdout + run.stderr)
            errors = [json.loads(path.read_text())['valgrind_error'] for path in snapshots]
            self.assertTrue(any('first' in error for error in errors), errors)
            self.assertTrue(any('second' in error for error in errors), errors)
            self.assertNotIn('现场保持暂停', run.stdout)
