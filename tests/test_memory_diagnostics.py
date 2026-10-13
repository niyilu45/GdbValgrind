import json
import tempfile
import unittest
from pathlib import Path

from inc.memory_diagnostics import memory_facts, analyze_fault_variables
from inc.workflow import repair_saved_diagnostics


class MemoryDiagnosticsTests(unittest.TestCase):
    def test_this_is_pointer_storage_not_pointee_and_requires_address_match(self):
        memory = memory_facts('Invalid read of size 8\nLocation 0x100 is 0 bytes inside local var "this"')
        self.assertEqual(memory['named_object']['name'], 'this')
        snapshot = {'association':{'status':'verified'}, 'memory':memory, 'frames':[],
                    'named_object_check':{'name':'this','address':'0x100','size_bytes':8,'value':'0x9999'}}
        result = analyze_fault_variables(snapshot)
        self.assertFalse(result['named_object']['exceeds_storage'])
        self.assertEqual(result['named_object']['address'],'0x100')
        snapshot['memory']['access_bytes'] = 16
        self.assertEqual(analyze_fault_variables(snapshot)['named_object']['outside_bytes'],8)
        snapshot['named_object_check']['address'] = '0x9999'
        self.assertNotIn('named_object',analyze_fault_variables(snapshot))

    def pointer_snapshot(self, size=208, width=4, state='allocated'):
        block = {'address':'0x100','start':256,'size':size,'status':state,
                 'evidence':"Address 0x100 is 0 bytes inside a block of size %d %s" % (size,"free'd" if state=='freed' else "alloc'd")}
        item = {'name':'st->ru[i].member','status':'unavailable','index_accesses':[{
            'expression':'st->ru[i]', 'actual_index':26, 'prefix_check':{
                'member_path':'member', 'target_storage':{'address':'0x1d4','size_bytes':4},
                'allocation':{'base':block}, 'address_calculation':{
                    'base_address':'0x100','index':26,'lower_bound':0,'stride':8,'member_offset':4}}}]}
        return {'association':{'status':'verified'},'memory':{'address':'0x1d4','access_bytes':width,'access':'read'},
                'frames':[{'line_checks':{'items':[item]}}]}

    def test_recovers_boundary_from_base_when_fault_has_no_block(self):
        result = analyze_fault_variables(self.pointer_snapshot())
        finding = result['boundary_findings'][0]
        self.assertEqual(finding['kind'], 'out_of_allocation')
        self.assertEqual(finding['outside_bytes'], 4)
        self.assertEqual(finding['actual_index'], 26)
        self.assertEqual(finding['block_index_range'], [0,25])
        self.assertEqual(finding['actual_offset'], 212)
        self.assertIn('4', result['cause'])

    def test_checks_actual_access_width_not_member_size(self):
        narrow = analyze_fault_variables(self.pointer_snapshot(size=216,width=4))['boundary_findings'][0]
        wide = analyze_fault_variables(self.pointer_snapshot(size=216,width=8))['boundary_findings'][0]
        self.assertEqual(narrow['kind'], 'inside_allocation')
        self.assertEqual(narrow['block_index_range'], [0,26])
        self.assertEqual(wide['kind'], 'out_of_allocation')
        self.assertEqual(wide['block_index_range'], [0,25])
        self.assertEqual(wide['outside_bytes'], 4)

    def test_rejects_unmatched_addresses_callers_and_unverified_stops(self):
        snapshot = self.pointer_snapshot()
        snapshot['memory']['address'] = '0x900'
        self.assertEqual(analyze_fault_variables(snapshot)['boundary_findings'], [])
        snapshot = self.pointer_snapshot()
        snapshot['frames'].insert(0, {})
        self.assertEqual(analyze_fault_variables(snapshot)['boundary_findings'], [])
        snapshot = self.pointer_snapshot()
        snapshot['association']['status'] = 'unverified'
        self.assertNotIn('boundary_findings', analyze_fault_variables(snapshot))

    def test_freed_pointer_and_direct_pointer_member(self):
        snapshot = self.pointer_snapshot(size=216,state='freed')
        item = snapshot['frames'][0]['line_checks']['items'][0]
        prefix = item.pop('index_accesses')[0]['prefix_check']
        item['name'] = 'ptr->member'
        item['pointer_targets'] = [{'expression':'ptr->member','target_storage':prefix['target_storage'],
                                    'allocation':prefix['allocation']['base']}]
        finding = analyze_fault_variables(snapshot)['boundary_findings'][0]
        self.assertEqual(finding['kind'], 'use_after_free')
        self.assertIsNone(finding['valid_start_offsets'])

    def test_non_heap_addresses_have_specific_explanations(self):
        for text, kind in [('Location 0x100 is 0 bytes inside local var "x"','stack'),
                           ('Location 0x100 is 0 bytes inside global var "x"','global'),
                           ("Address 0x100 is not stack'd, malloc'd or (recently) free'd",'unallocated'),
                           ('Address 0x0 is not mapped','null')]:
            with self.subTest(kind=kind):
                result = memory_facts('Invalid read of size 8\n'+text)
                self.assertEqual(result['storage_category'], kind)
                self.assertNotIn('missing_reason', result)
                self.assertNotIn('block_bytes', result)
        result = memory_facts('Invalid read of size 8\nLocation 0x100 is unknown',
                             'Location 0x100 is 0 bytes inside global var "x"')
        self.assertEqual(result['storage_category'], 'global')
        wrong = memory_facts('Invalid read of size 8\nLocation 0x100 is unknown',
                             'Location 0x200 is 0 bytes inside global var "x"')
        self.assertEqual(wrong['storage_category'], 'unknown')

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
