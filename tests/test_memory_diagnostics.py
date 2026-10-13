import json
import tempfile
import unittest
from pathlib import Path

from inc.memory_diagnostics import memory_facts
from inc.workflow import repair_saved_diagnostics


class MemoryDiagnosticsTests(unittest.TestCase):
    def test_location_diagnostic_keeps_address_without_inventing_bounds(self):
        result = memory_facts('Invalid read of size 8\n at 0x444: run\n==12== Location 0x1234 is 0 bytes inside local var "item"')
        self.assertEqual(result['address'], '0x1234')
        self.assertNotIn('block_bytes', result)
        self.assertIn('item', result['address_description'])

    def test_same_address_lookup_supplies_block(self):
        result = memory_facts('Invalid write of size 8\nAddress 0x110 is not described',
                              "Location 0x110 is 0 bytes after a block of size 16 alloc'd")
        self.assertEqual(result['block_bytes'], 16)
        self.assertEqual(result['block_start_address'], '0x100')
        self.assertEqual(result['actual_offset_bytes'], 16)

    def test_lookup_cannot_invent_address_or_use_neighbor(self):
        lookup = "Address 0x300 is 0 bytes inside a block of size 288 alloc'd"
        self.assertNotIn('address', memory_facts('Invalid read of size 8\n at 0x444: run', lookup))
        self.assertNotIn('block_bytes', memory_facts('Invalid read of size 8\nLocation 0x100 is on stack', lookup))
        self.assertEqual(memory_facts('Invalid read of size 4\nAddress 0x0 is not mapped')['address'], '0x0')

    def test_saved_sidecar_repaired_without_modifying_variable_values(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            snapshot = {'valgrind_error':'Invalid read of size 8\nLocation 0x100 is inside local var "x"',
                        'memory':{'access':'read','access_bytes':8},
                        'association':{'status':'verified'},
                        'frames':[{'line_checks':{'items':[{'name':'x','value':'7','storage':{'address':'0x100','size_bytes':4}}]}}]}
            path = directory / 'capture-updates.js'
            path.write_text('window.aivCaptureUpdate('+json.dumps({'live':False,'replay_id':'old','items':[{'snapshot':snapshot}]})+');',encoding='utf-8')
            repair_saved_diagnostics(directory)
            text = path.read_text(encoding='utf-8')
            repaired = json.loads(text[len('window.aivCaptureUpdate('):-2])
            result = repaired['items'][0]['snapshot']
            self.assertEqual(result['memory']['address'], '0x100')
            self.assertEqual(result['frames'], snapshot['frames'])
            self.assertEqual(result['fault_analysis']['status'], 'single_candidate')
            self.assertEqual(repaired['replay_id'], 'old')
            repair_saved_diagnostics(directory)
            self.assertEqual(path.read_text(encoding='utf-8'), text)
