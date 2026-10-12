import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from inc.precision import select_precision, probe_precision
from inc.fault_context import FAULT_CONTEXT_SCRIPT
import re


class PrecisionTests(unittest.TestCase):
    HELP = '''
 --vgdb=no|yes|full
 --vex-iropt-register-updates=mode
    allregs-at-mem-access
    allregs-at-each-insn
 --vgdb-shadow-registers=yes|no
 --read-var-info=yes|no
 --read-inline-info=yes|no
 --num-callers=number
 --keep-debuginfo=yes|no
 --keep-stacktraces=alloc|free|alloc-and-free
 --freelist-vol=number
 --expensive-definedness-checks=no|auto|yes
'''

    def test_supported_installations_select_full_without_conflicting_register_flag(self):
        for version in ('3.10.0', '3.11.0', '3.12.0', '3.14.0', '3.22.0'):
            with self.subTest(version=version):
                p = select_precision('valgrind-'+version, self.HELP, True)
                self.assertIn('--vgdb=full', p['options'])
                self.assertEqual(p['register_precision'], 'allregs-at-each-insn')
                self.assertIn('--keep-stacktraces=alloc-and-free', p['options'])
                self.assertNotIn('--vgdb=yes', p['options'])

    def test_partial_vendor_build_and_unknown_version_degrade_honestly(self):
        p = select_precision('valgrind-3.22.0', '--vgdb=yes|no\n--vex-iropt-register-updates=allregs-at-mem-access', True)
        self.assertEqual(p['register_precision'], 'allregs-at-mem-access')
        self.assertIn('--vgdb=yes', p['options'])
        self.assertNotIn('--keep-debuginfo=yes', p['options'])
        self.assertTrue(p['skipped'])
        self.assertEqual(select_precision('unknown', self.HELP, True)['register_precision'], 'unknown')
        misleading = select_precision('valgrind-3.10.0', '--vgdb-prefix=full', True)
        self.assertNotIn('--vgdb=full', misleading['options'])

    def test_collection_does_not_stop_target_for_gdb(self):
        p = select_precision('valgrind-3.10.0', self.HELP)
        self.assertFalse(any('vgdb' in flag for flag in p['options']))

    @patch('inc.precision.subprocess.run', side_effect=subprocess.TimeoutExpired('valgrind', 5))
    def test_probe_timeout_has_bounded_fallback(self, run):
        p = probe_precision('/bin/valgrind', True)
        self.assertEqual(p['register_precision'], 'unknown')
        self.assertEqual(run.call_count, 2)


class InstructionTests(unittest.TestCase):
    def context(self, asm, architecture='i386:x86-64', precise=True, fault='0x170'):
        calls = []
        gdb = SimpleNamespace(parameter=lambda _: 'intel', execute=lambda c, **k: calls.append(c))
        arch = SimpleNamespace(name=lambda: architecture, disassemble=lambda *a, **k:[{'addr':0x1000,'length':4,'asm':asm}])
        frame = SimpleNamespace(pc=lambda:0x1000, architecture=lambda:arch, select=lambda:None,
                                read_register=lambda name: {'rax':0x100,'rcx':26,'eax':0xfffffff0}[name])
        scope = {'gdb':gdb, 're':re, 'capture_execute':lambda c:'raw evidence', 'disconnected':lambda s:False,
                 'parse_vbits':lambda raw, count: [None]+[0]*(count-1)}
        exec(FAULT_CONTEXT_SCRIPT, scope)
        result = scope['capture_fault_context'](frame, {'access':'read','address':fault,'access_bytes':8},
                                               {'register_precision':'allregs-at-each-insn'} if precise else {})
        return result, calls

    def test_runtime_index_and_flavor_restored(self):
        result, calls = self.context('mov 0x8(%rax,%rcx,4),%edx')
        op = result['operands'][0]
        self.assertEqual(op['index_value'], 26)
        self.assertEqual(op['address'], '0x170')
        self.assertTrue(op['matches_fault_address'])
        self.assertTrue(op['registers_precise'])
        self.assertEqual(calls[-1], 'set disassembly-flavor intel')
        self.assertEqual(result['access_check']['unaddressable_offsets'], [0])
        self.assertEqual(result['access_check']['queried_bytes'], 8)

    def test_rip_relative_and_address_size_override(self):
        result, _ = self.context('mov 0x10(%rip),%eax', fault='0x1014')
        self.assertTrue(result['operands'][0]['matches_fault_address'])
        result, _ = self.context('mov 0x20(%eax),%edx', fault='0x10')
        self.assertTrue(result['operands'][0]['matches_fault_address'])

    def test_unknown_precision_mismatch_segments_and_unsupported_instructions(self):
        result, _ = self.context('mov (%rax),%edx', precise=False)
        self.assertFalse(result['operands'][0]['registers_precise'])
        self.assertFalse(result['operands'][0]['matches_fault_address'])
        for asm in ('mov %fs:0x8(%rax),%edx', 'lea (%rax),%edx', 'rep movsb'):
            self.assertEqual(self.context(asm)[0]['operands'], [])
        self.assertEqual(self.context('ldr x0,[x1]', 'aarch64')[0]['operands'], [])
