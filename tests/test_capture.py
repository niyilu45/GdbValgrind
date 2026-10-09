import json
from pathlib import Path
import tempfile
import subprocess
import sys
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from inc import core as av
from inc import cli


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.messages = []
        self.count = 0
        self.error = 'Invalid write of size 4\n at 0x100: bad (demo.c:9)\nAddress 0x200 is 0 bytes after a block of size 4 alloc\'d\n at 0x300: malloc\n by 0x400: bad (demo.c:7)'
        available = SimpleNamespace(type='int', is_optimized_out=False, format_string=lambda **kwargs: '4')
        optimized = SimpleNamespace(type='int', is_optimized_out=True)
        markup = SimpleNamespace(type='char *', is_optimized_out=False, format_string=lambda **kwargs: '</pre><script>alert(1)</script>')
        symbols = [SimpleNamespace(name=name, is_argument=False, is_variable=True) for name in ('i', 'gone', 'html')]

        class Block(list):
            is_global = False
            is_static = False
            superblock = None

        self.frame = SimpleNamespace(name=lambda: 'bad', find_sal=lambda: SimpleNamespace(symtab=SimpleNamespace(fullname=lambda: 'demo.c'), line=9),
                                     block=lambda: Block(symbols), read_var=lambda symbol: {'i': available, 'gone': optimized, 'html': markup}[symbol.name], older=lambda: None)
        self.gdb = SimpleNamespace(execute=self.execute, write=self.messages.append,
                                   TYPE_CODE_INT=1, TYPE_CODE_BOOL=2, TYPE_CODE_ENUM=3, TYPE_CODE_PTR=4,
                                   TYPE_CODE_CHAR=5, TYPE_CODE_ARRAY=6, TYPE_CODE_STRUCT=7, TYPE_CODE_UNION=8,
                                   events=SimpleNamespace(stop=SimpleNamespace(connect=lambda callback: None)),
                                   selected_thread=lambda: SimpleNamespace(ptid=(42, 42, 0)), newest_frame=lambda: self.frame)
        self.scope = {'_capture_config': {'directory': str(self.root), 'requested_error_id': 'historical-id'}}
        with patch.dict('sys.modules', {'gdb': self.gdb}):
            exec(av.GDB_CAPTURE_SCRIPT, self.scope)
        self.collector = self.scope['_aivalgrind_capture']

    def execute(self, command, **kwargs):
        if command == 'monitor v.info n_errs_found':
            return 'n_errs_found %s n_errs_shown %s' % (self.count, self.count)
        if command == 'monitor v.info last_error':
            return self.error
        return 'diagnostic output'

    def test_first_snapshot_values_facts_and_html(self):
        self.count = 1
        self.collector.on_stop(None)
        data = json.loads((self.root / 'error-0001.json').read_text(encoding='utf-8'))
        self.assertEqual(data['memory']['access_bytes'], 4)
        self.assertEqual(data['memory']['relation'], 'after')
        self.assertEqual(data['frames'][0]['variables'][0]['value'], '4')
        self.assertEqual(data['frames'][0]['variables'][1]['status'], 'optimized_out')
        html = (self.root / 'error-0001.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(data['requested_error_id'], 'historical-id')

    def test_repeated_errors_keep_only_first(self):
        self.collector.on_stop(None)  # startup / source breakpoint
        self.assertFalse(list(self.root.glob('*.json')))
        self.count = 1
        self.collector.on_stop(None)
        first = (self.root / 'error-0001.json').read_bytes()
        self.count = 2
        self.error = self.error.replace('0x200', '0x900').replace('0x100', '0x700')
        self.collector.on_stop(None)
        self.collector.on_stop(None)  # a later breakpoint with unchanged counter
        self.assertEqual(len(list(self.root.glob('*.json'))), 1)
        self.assertEqual((self.root / 'error-0001.json').read_bytes(), first)
        self.count = 3
        self.error = self.error.replace('demo.c:9', 'demo.c:10')
        self.collector.on_stop(None)
        self.assertEqual(len(list(self.root.glob('*.json'))), 2)

    def test_memory_relations(self):
        facts = self.scope['memory_facts']
        self.assertEqual(facts('Invalid read of size 8\nAddress 0x123 is 4 bytes before a block of size 1,024 alloc\'d')['block_bytes'], 1024)
        self.assertIn('已释放', facts('Address 0x123 is 4 bytes inside a block of size 16 free\'d')['explanation'])
        self.assertIn('超出了', facts('Invalid read of size 8\nAddress 0x123 is 12 bytes inside a block of size 16 alloc\'d')['explanation'])
        self.assertNotIn('explanation', facts('Conditional jump or move depends on uninitialised value(s)'))

    def test_unreadable_counter_does_not_guess(self):
        self.gdb.execute = lambda *args, **kwargs: 'unsupported monitor command'
        self.collector.on_stop(None)
        self.assertFalse(list(self.root.glob('*.json')))
        self.assertTrue(any('无法读取错误计数' in x for x in self.messages))

    def test_unreadable_error_does_not_mark_seen(self):
        self.count = 1
        self.error = '[unavailable: unsupported]'
        self.collector.on_stop(None)
        self.assertFalse(self.collector.seen)
        self.assertFalse(list(self.root.glob('*.json')))

    def test_script_paths_and_commands(self):
        first, script = av.prepare_capture(self.root / 'with space', "id'\nnewline")
        second, _ = av.prepare_capture(self.root / 'with space', 'id')
        self.assertNotEqual(first, second)
        compile(script.read_text(encoding='utf-8'), str(script), 'exec')
        commands = av.debug_commands({}, 42, '/tmp/test', '/usr/bin/vgdb', capture_script=script)
        self.assertIn('monitor v.set vgdb-error 1', commands)
        self.assertFalse(any(c.startswith('break ') for c in commands))
        self.assertEqual(commands[-1], 'continue')

    def test_cli_exposes_capture_mode(self):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'aivalgrind.py'), 'serve', '--help'],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'--auto-values', result.stdout)
        self.assertIn(b'--capture-dir', result.stdout)

    def value_type(self, code=1, size=4, **kwargs):
        t = SimpleNamespace(code=code, sizeof=size, **kwargs)
        t.strip_typedefs = lambda: t
        return t

    def inspect_init(self, output, t=None, address=0x100, budget=None):
        self.gdb.execute = lambda command, **kwargs: output
        value = SimpleNamespace(type=t or self.value_type(), address=address)
        return self.scope['check_initialization'](value, budget or {'queries': 10, 'bytes': 4096})

    def test_vbits_initialized_uninitialized_and_partial_bits(self):
        self.assertEqual(self.inspect_init('00000000')['status'], 'defined')
        self.assertEqual(self.inspect_init('ffffffff')['status'], 'undefined')
        result = self.inspect_init('000001ff')
        self.assertEqual(result['status'], 'partially_undefined')
        self.assertEqual(result['undefined_byte_offsets'], [2, 3])
        self.assertEqual(result['undefined_bits'], 9)

    def test_vbits_bad_or_missing_response_and_unaddressable(self):
        for output in ('', 'unsupported command', '00ff', 'Address 0x100 len 4 defined'):
            self.assertEqual(self.inspect_init(output)['status'], 'unknown')
        result = self.inspect_init('00__ffff\nAddress 0x100 len 4 has 1 bytes unaddressable')
        self.assertEqual(result['status'], 'unaddressable')
        self.assertEqual(result['unaddressable_byte_offsets'], [1])
        self.assertEqual(self.inspect_init('00000000', address=None)['status'], 'unknown')

    def test_structure_padding_excluded_and_real_member_detected(self):
        char = self.value_type(5, 1)
        integer = self.value_type()
        fields = [SimpleNamespace(bitpos=0, bitsize=0, type=char), SimpleNamespace(bitpos=32, bitsize=0, type=integer)]
        structure = self.value_type(7, 8, fields=lambda: fields)
        result = self.inspect_init('00ffffff 00000000', structure)
        self.assertEqual(result['status'], 'defined')
        self.assertEqual(result['ignored_padding_byte_offsets'], [1, 2, 3])
        result = self.inspect_init('00ffffff ffffffff', structure)
        self.assertEqual(result['status'], 'partially_undefined')
        self.assertEqual(result['undefined_byte_offsets'], [4, 5, 6, 7])

    def test_union_bitfield_register_and_budget_are_not_assumed_defined(self):
        self.assertEqual(self.inspect_init('00000000', self.value_type(8))['status'], 'unknown')
        field = SimpleNamespace(bitpos=0, bitsize=3, type=self.value_type())
        self.assertEqual(self.inspect_init('00000000', self.value_type(7, fields=lambda: [field]))['status'], 'unknown')
        result = self.inspect_init('00000000', budget={'queries': 0, 'bytes': 4096})
        self.assertEqual(result['status'], 'unknown')

    def test_large_array_only_prefix_checked(self):
        array = self.value_type(6, 400, target=lambda: self.value_type(), range=lambda: (0, 99))
        result = self.inspect_init('00' * 256, array)
        self.assertEqual(result['status'], 'partial_check')
        self.assertEqual(result['observed_status'], 'defined')
        self.assertFalse(result['complete'])

    def test_uninitialized_error_report_and_first_only(self):
        self.count = 1
        self.error = 'Conditional jump or move depends on uninitialised value(s)\n at 0x100: bad (demo.c:9)'
        original = self.execute
        self.gdb.execute = lambda command, **kwargs: 'ffffffff' if 'get_vbits' in command else original(command, **kwargs)
        self.frame.read_var = lambda symbol: SimpleNamespace(type=self.value_type(), address=0x100,
                                                             is_optimized_out=False, format_string=lambda **kwargs: '0')
        self.collector.on_stop(None)
        data = json.loads((self.root / 'error-0001.json').read_text(encoding='utf-8'))
        self.assertTrue(data['initialization_analysis']['error_is_uninitialized_use'])
        self.assertEqual(data['frames'][0]['variables'][0]['value'], '0')
        self.assertEqual(data['frames'][0]['variables'][0]['initialization']['status'], 'undefined')
        self.assertTrue(data['initialization_analysis']['candidates'])
        self.assertIn('未初始化', (self.root / 'error-0001.txt').read_text(encoding='utf-8'))
        self.count = 2
        self.collector.on_stop(None)
        self.assertEqual(len(list(self.root.glob('*.json'))), 1)

    def test_struct_unused_fields_only_warning_and_direct_use_evidence(self):
        fields = [SimpleNamespace(name='ready', bitpos=0, bitsize=0, type=self.value_type()),
                  SimpleNamespace(name='unused', bitpos=32, bitsize=0, type=self.value_type())]
        info = self.inspect_init('00000000 ffffffff', self.value_type(7, 8, fields=lambda: fields))
        snapshot = {'valgrind_error': 'Conditional jump or move depends on uninitialised value(s)',
                    'frames': [{'index': 0, 'variables': [{'name': 'item', 'initialization': info}]}]}
        analyze = self.scope['analyze_initialization_use']
        result = analyze(snapshot)
        self.assertTrue(result['error_is_uninitialized_use'])
        self.assertEqual(result['event_severity'], 'error')
        self.assertEqual(result['warning_count'], 1)
        self.assertEqual(result['findings'][0]['name'], 'item.unused')
        self.assertEqual(result['findings'][0]['usage'], 'not_observed')
        snapshot['valgrind_error'] = 'Syscall param write(buf) points to uninitialised byte(s)\nAddress 0x104 is 4 bytes inside a block of size 8 alloc\'d'
        self.assertEqual(analyze(snapshot)['findings'][0]['severity'], 'error')
        # An address in a different member must not upgrade this warning.
        snapshot['valgrind_error'] = snapshot['valgrind_error'].replace('0x104', '0x100')
        self.assertEqual(analyze(snapshot)['findings'][0]['severity'], 'warning')
        # Generic allocation/origin addresses are not evidence of operand use.
        snapshot['valgrind_error'] = 'Conditional jump depends on uninitialised value(s)\nAddress 0x104'
        self.assertEqual(analyze(snapshot)['findings'][0]['severity'], 'warning')


@unittest.skipUnless(sys.platform == 'linux' and all(shutil.which(n) for n in ('cc', 'valgrind', 'vgdb', 'gdb')), '需要 Linux 与 Valgrind/GDB')
class InitializationIntegrationTests(unittest.TestCase):
    def test_uninitialized_use_and_unused_struct_member(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, binary = root / 'uninit.c', root / 'uninit'
            source.write_text('''#include <stdio.h>
int main(void) {
    struct { char marker; int unused; int used; } item;
    volatile int trigger;
    item.marker = 'x';
    item.used = 7;
    if (trigger) puts("branch");
    printf("%c %d", item.marker, item.used);
    return 0;
}
''')
            subprocess.run(['cc', '-g', '-O0', str(source), '-o', str(binary)], check=True, capture_output=True)
            directory, collector = av.prepare_capture(root / 'captures', 'test')
            prefix = str(root / 'vgdb')
            vg = subprocess.Popen(['valgrind', '--track-origins=yes', '--vgdb=yes', '--vgdb-error=0', '--vgdb-prefix='+prefix, str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                commands = av.debug_commands({}, vg.pid, prefix, shutil.which('vgdb'), capture_script=collector)
                script = root / 'run.gdb'
                script.write_text('\n'.join(commands + ['quit']) + '\n')
                run = subprocess.run(['gdb', '-q', '-nx', '-batch', '-x', str(script), str(binary)], capture_output=True, text=True, timeout=45)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                files = list(directory.glob('error-*.json'))
                self.assertEqual(len(files), 1, run.stdout + run.stderr)
                snapshot = json.loads(files[0].read_text(encoding='utf-8'))
                analysis = snapshot['initialization_analysis']
                self.assertTrue(analysis['error_is_uninitialized_use'])
                field = next(f for f in analysis['findings'] if f['name'] == 'item.unused')
                self.assertEqual(field['severity'], 'warning')
                self.assertFalse(any(f['name'] == 'item.used' for f in analysis['findings']))
            finally:
                vg.terminate()
                try:
                    vg.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    vg.kill()
                    vg.wait()


if __name__ == '__main__':
    unittest.main()
