import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from inc.navigation import GDB_NAVIGATION_SCRIPT
from inc import core


class FakeBreakpoint:
    def __init__(self, spec, **kwargs):
        self.spec = spec


class FakeCommand:
    def __init__(self, *args):
        pass

    def dont_repeat(self):
        pass


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.restart = Path(self.directory.name) / 'restart.json'
        self.gdb = SimpleNamespace(Breakpoint=FakeBreakpoint, Command=FakeCommand,
            COMMAND_RUNNING=1, GdbError=ValueError, string_to_argv=lambda s: s.split(),
            events=SimpleNamespace(exited=SimpleNamespace(connect=MagicMock())),
            execute=MagicMock(), write=MagicMock())
        self.config = {'initial': 'b', 'auto_values': False, 'restart_file': str(self.restart),
            'entries': [{'id': key, 'order': i, 'default': '-function '+key,
                         'frames': {'0:0': '-function '+key}} for i, key in enumerate('abc', 1)]}
        self.scope = {'_navigation_config': self.config}
        with patch.dict('sys.modules', {'gdb': self.gdb}):
            exec(GDB_NAVIGATION_SCRIPT, self.scope)
        self.nav = self.scope['_aiv_navigation']

    def test_forward_continues_and_tracks_only_reached_location(self):
        self.assertTrue(self.nav.active.stop())
        with patch('builtins.input', side_effect=AssertionError('forward must not prompt')):
            self.nav.navigate('c 0:0')
        self.gdb.execute.assert_called_once_with('continue')
        self.assertEqual(self.nav.current, 'b')
        self.assertFalse(self.nav.breakpoints[1].stop())
        self.assertTrue(self.nav.active.stop())
        self.assertEqual(self.nav.current, 'c')

    def test_backward_no_keeps_paused_session(self):
        self.nav.active.stop()
        active = self.nav.active
        with patch('builtins.input', return_value='n') as prompt:
            self.nav.navigate('a')
        prompt.assert_called_once()
        self.gdb.execute.assert_not_called()
        self.assertIs(self.nav.active, active)
        self.assertFalse(self.restart.exists())

    def test_backward_yes_requests_supervisor_restart(self):
        self.nav.active.stop()
        with patch('builtins.input', return_value='y'):
            self.nav.navigate('a 0:0')
        self.assertEqual(json.loads(self.restart.read_text()), {'id': 'a', 'frame': '0:0'})
        self.assertEqual(self.gdb.execute.call_args.args, ('quit',))

    def test_invalid_target_or_frame_never_resumes(self):
        for command in ('other', 'a invalid', 'a 0:0 extra'):
            with self.assertRaises(ValueError):
                self.nav.navigate(command)
        self.gdb.execute.assert_not_called()

    def test_exited_target_and_interrupted_confirmation(self):
        self.nav.on_exit(None)
        with patch('builtins.input', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.nav.navigate('c')
        self.assertFalse(self.restart.exists())
        self.gdb.execute.assert_not_called()

    def test_restart_cleans_old_session_before_launch(self):
        args = SimpleNamespace(navigation_errors=[{'id': 'a'}, {'id': 'b'}], frame=None, auto_values=True)
        events = []
        class Session:
            def __enter__(self):
                events.append('enter')
                return self
            def __exit__(self, *exc):
                events.append('cleanup')
        def run(error, passed, processes):
            events.append(error['id'])
            return {'id': 'a', 'frame': '0:0'} if error['id'] == 'b' else 0
        with patch.object(core, 'check_debug_environment'), patch.object(core, 'ProcessSession', Session), patch.object(core, '_run_debug', side_effect=run):
            self.assertEqual(core.run_debug({'id': 'b'}, args), 0)
        self.assertEqual(events, ['enter', 'b', 'cleanup', 'enter', 'a', 'cleanup'])
        self.assertTrue(args.auto_values)

    def test_prepared_script_preserves_report_order_and_selected_frame(self):
        report = core.load_report(Path(__file__).resolve().parents[1] / 'examples/sample.xml')
        error = report['errors'][0]
        script, restart = core.prepare_navigation(self.directory.name, report['errors'], error, core.pick_frame(error), False)
        compile(script.read_text(encoding='utf-8'), str(script), 'exec')
        self.assertEqual(restart.name, 'restart.json')


@unittest.skipUnless(sys.platform == 'linux' and all(shutil.which(n) for n in ('cc', 'valgrind', 'vgdb', 'gdb')), '需要 Linux 和 GDB/Valgrind')
class NavigationIntegrationTests(unittest.TestCase):
    def test_real_forward_and_backward_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'demo.c'
            source.write_text('void first(void) { volatile int a=1; }\nvoid second(void) { volatile int b=2; }\nint main(void) { first(); second(); return 0; }\n')
            binary = root / 'demo'
            subprocess.run(['cc', '-g', '-O0', str(source), '-o', str(binary)], check=True, timeout=30)
            errors = [{'id': fn, 'stacks': [{'frames': [{'fn': fn}]}]} for fn in ('first', 'second')]
            navigation, restart = core.prepare_navigation(root, errors, errors[0], {'fn': 'first'}, False)
            prefix = str(root / 'vgdb')
            vg = subprocess.Popen(['valgrind', '--vgdb=yes', '--vgdb-error=0', '--vgdb-prefix='+prefix, str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                commands = core.debug_commands({'fn': 'first'}, vg.pid, prefix, shutil.which('vgdb'))
                commands.remove(core.breakpoint_command({'fn': 'first'}))
                commands.insert(-1, "python exec(compile(open("+repr(str(navigation))+", encoding='utf-8').read(), 'nav', 'exec'))")
                commands += ["python assert _aiv_navigation.current == 'first'", 'aiv-goto second',
                             "python assert _aiv_navigation.current == 'second'",
                             "python import builtins; builtins.input = lambda prompt: 'n'", 'aiv-goto first',
                             "python assert not Path(_navigation_config['restart_file']).exists()",
                             "python builtins.input = lambda prompt: 'y'", 'aiv-goto first']
                script = root / 'session.gdb'
                script.write_text('\n'.join(commands), encoding='utf-8')
                result = subprocess.run(['gdb', '-q', '-nx', '-nh', '-batch', '-x', str(script), str(binary)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertNotIn('AssertionError', result.stdout+result.stderr)
                self.assertEqual(json.loads(restart.read_text()), {'id': 'first', 'frame': None})
            finally:
                if vg.poll() is None:
                    vg.terminate()
                try:
                    vg.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    vg.kill()
                    vg.wait(timeout=3)
