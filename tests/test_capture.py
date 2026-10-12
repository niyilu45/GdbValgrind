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
    def test_fault_candidates_require_verified_top_frame_address(self):
        analyze = self.scope['analyze_fault_variables']
        item = {'name': 'arr[i].member', 'storage': {'address':'0x100', 'size_bytes':4}}
        snapshot = {'association':{'status':'verified'},
                    'memory':{'address':'0x100', 'access_bytes':8, 'access':'read',
                              'explanation':'block boundary crossed'},
                    'frames':[{'line_checks':{'items':[item]}}]}
        result = analyze(snapshot)
        self.assertEqual(result['status'], 'single_candidate')
        self.assertTrue(result['candidates'][0]['exceeds_expression'])
        self.assertEqual(result['last_address'], '0x107')
        del item['storage']
        item['index_accesses'] = [{'expression':'arr[i]', 'prefix_check':{
            'member_path':'member', 'target_storage':{'address':'0x100','size_bytes':4}}}]
        self.assertEqual(analyze(snapshot)['status'], 'single_candidate')
        snapshot['frames'][0]['line_checks']['items'].append(dict(item, name='alias', storage={'address':'0x100','size_bytes':4}))
        self.assertEqual(analyze(snapshot)['status'], 'multiple_candidates')
        snapshot['frames'].insert(0, {'line_checks':{'items':[]}})
        self.assertEqual(analyze(snapshot)['status'], 'unknown')
        snapshot['frames'].pop(0)
        snapshot['association']['status'] = 'unverified'
        self.assertEqual(analyze(snapshot)['candidates'], [])
        snapshot['association']['status'] = 'verified'
        snapshot['memory']['address'] = '0x104'
        self.assertEqual(analyze(snapshot)['status'], 'unknown')

    def test_allocation_ownership_does_not_accept_neighboring_live_block(self):
        answers = {0x108:"Address 0x108 is 8 bytes inside a block of size 32 alloc'd",
                   0x124:"Address 0x124 is 4 bytes inside a block of size 8 alloc'd",
                   0x127:"Address 0x127 is 7 bytes inside a block of size 8 alloc'd"}
        calls = []
        def execute(command, **kwargs):
            calls.append(command)
            return answers.get(int(command.split()[-1],16),'unknown command')
        self.gdb.execute = execute
        budget = {}
        result = self.scope['check_allocation_range'](0x108,8,4,4,3,budget)
        self.assertEqual(result['status'],'outside_block')
        self.assertEqual(result['ranges'],[{'start':0,'end':2,'status':'same_block'},
                                          {'start':3,'end':3,'status':'outside_block'}])
        self.assertEqual(result['target']['status'],'allocated')
        self.scope['check_allocation_range'](0x108,8,4,4,3,budget)
        self.assertEqual(len(calls),3)

    def test_allocation_requires_exact_containing_block_and_detects_freed(self):
        lookup = self.scope['allocation_location']
        for answer in ("Address 0x100 is 0 bytes after a block of size 32 alloc'd",
                       "Address 0x200 is 0 bytes inside a block of size 32 alloc'd",
                       'Address 0x100 is on thread 1 stack', 'unknown monitor command'):
            self.gdb.execute = lambda *args, answer=answer, **kwargs:answer
            self.assertEqual(lookup(0x100,{})['status'],'unknown')
        self.gdb.execute = lambda *args, **kwargs:"Address 0x100 is 0 bytes inside a block of size 32 free'd"
        result = self.scope['check_allocation_range'](0x100,8,0,4,3,{})
        self.assertEqual(result['status'],'freed')
        self.assertEqual(result['ranges'],[{'start':0,'end':3,'status':'freed'}])

    def test_allocation_member_full_width_and_same_live_block(self):
        def execute(command, **kwargs):
            address = int(command.split()[-1],16)
            start = 0x100 if address < 0x120 else 0x120
            return "Address %s is %d bytes inside a block of size 32 alloc'd" % (hex(address),address-start)
        self.gdb.execute = execute
        check = self.scope['check_allocation_range']
        self.assertEqual(check(0x100,8,4,4,3,{})['status'],'same_block')
        self.assertEqual(check(0x100,8,4,8,3,{})['status'],'outside_block')

    def array_prefix(self, raw, index, high, element=None, budget=None, member_path=()):
        element = element or self.value_type()
        array = SimpleNamespace(address=0x100, type=self.value_type(6, (high+1)*element.sizeof, target=lambda: element))
        calls = []
        def execute(command, **kwargs):
            words = command.split()
            address, length = int(words[-2], 16), int(words[-1])
            calls.append((address, length))
            return ''.join(raw[address-0x100:address-0x100+length])
        self.gdb.execute = execute
        result = self.scope['check_array_prefix'](array, index, 0, high, budget if budget is not None else {}, member_path)
        return result, calls

    def test_array_prefix_scans_beyond_256_bytes_and_merges_ranges(self):
        raw = ['00'] * 1200
        raw[1024] = 'ff'
        raw[1028] = 'ff'
        raw[1196] = '__'
        result, calls = self.array_prefix(raw, 299, 299)
        self.assertEqual(calls, [(0x100, 1200)])
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(result['checked_elements'], 300)
        self.assertEqual(result['ranges'], [
            {'start':0,'end':255,'status':'defined'},
            {'start':256,'end':257,'status':'undefined'},
            {'start':258,'end':298,'status':'defined'},
            {'start':299,'end':299,'status':'unaddressable'}])

    def test_array_prefix_never_reads_out_of_bounds_and_marks_budget_limit(self):
        result, calls = self.array_prefix(['00']*40, 100, 9)
        self.assertEqual(calls, [(0x100, 40)])
        self.assertEqual(result['ranges'][-1], {'start':10,'end':100,'status':'out_of_bounds'})
        budget = {'array_prefix': {'queries':10,'bytes':12,'ranges':512}}
        result, calls = self.array_prefix(['00']*40, 9, 9, budget=budget)
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['checked_elements'], 3)
        self.assertEqual(result['ranges'][-1], {'start':3,'end':9,'status':'unchecked'})
        self.assertEqual(calls, [(0x100, 12)])
        result, calls = self.array_prefix(['00']*40, -1, 9)
        self.assertEqual(calls, [])
        self.assertFalse(result['coverage_complete'])

    def test_array_prefix_merges_across_batch_boundaries(self):
        result, calls = self.array_prefix(['00']*4800, 1199, 1199)
        self.assertEqual(calls, [(0x100,4096),(0x1100,704)])
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(result['ranges'], [{'start':0,'end':1199,'status':'defined'}])

    def test_index_26_with_bounds_287_checks_all_27_elements(self):
        result, calls = self.array_prefix(['00']*1152, 26, 287)
        self.assertEqual(result['requested'], [0,26])
        self.assertEqual(result['checked_elements'], 27)
        self.assertEqual(result['element_bytes'], 4)
        self.assertEqual(result['checked_bytes'], 108)
        self.assertEqual(result['target_bytes'], 108)
        self.assertEqual(result['queried_bytes'], 108)
        self.assertEqual(result['start_address'], '0x100')
        self.assertEqual(result['end_address'], '0x16b')
        self.assertEqual(result['queries'], [{'address':'0x100','bytes':108}])
        self.assertEqual(result['ranges'], [{'start':0,'end':26,'status':'defined'}])
        self.assertEqual(calls, [(0x100,108)])
        # A first result segment of 0..0 must not hide the remainder.
        raw = ['00']*4 + ['ff']*104
        result, _ = self.array_prefix(raw, 26, 287)
        self.assertEqual(result['ranges'], [{'start':0,'end':0,'status':'defined'},
                                          {'start':1,'end':26,'status':'undefined'}])

    def test_array_prefix_struct_padding_is_not_uninitialized_member(self):
        fields = [SimpleNamespace(name='x',bitpos=0,bitsize=0,type=self.value_type(5,1)),
                  SimpleNamespace(name='y',bitpos=32,bitsize=0,type=self.value_type())]
        element = self.value_type(7,8,fields=lambda:fields)
        raw = ['00'] + ['ff']*3 + ['00']*4
        result, calls = self.array_prefix(raw*100, 99, 99, element)
        self.assertEqual(result['ranges'], [{'start':0,'end':99,'status':'defined'}])
        self.assertEqual(calls, [(0x100,800)])

    def test_array_prefix_query_failure_preserves_unchecked_range(self):
        result, _ = self.array_prefix([], 4, 4)
        self.assertEqual(result['checked_elements'], 0)
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['ranges'], [{'start':0,'end':4,'status':'unknown'}])
        self.assertEqual(result['failed_elements'],5)

    def test_second_member_query_failure_does_not_skip_remaining_25(self):
        element = self.value_type(7,8,fields=lambda:[SimpleNamespace(name='member',bitpos=0,bitsize=0,type=self.value_type())])
        raw = ['00']*(27*8)
        raw[8:12] = ['!']*4  # Simulate malformed/unavailable monitor response for arr[1].member.
        result,calls = self.array_prefix(raw,26,287,element,member_path=('member',))
        self.assertEqual(len(calls),27)
        self.assertEqual(calls[-1],(0x100+26*8,4))
        self.assertEqual(result['attempted_elements'],27)
        self.assertEqual(result['checked_elements'],26)
        self.assertEqual(result['failed_elements'],1)
        self.assertEqual(result['ranges'],[{'start':0,'end':0,'status':'defined'},
                                         {'start':1,'end':1,'status':'unknown'},
                                         {'start':2,'end':26,'status':'defined'}])
        self.assertEqual(result['query_errors'][0]['response'],'!!!!')
        self.assertFalse(result['coverage_complete'])

    def test_struct_array_member_only_checks_same_member_in_previous_elements(self):
        integer = self.value_type()
        fields = [SimpleNamespace(name='member',bitpos=0,bitsize=0,type=integer),
                  SimpleNamespace(name='other',bitpos=32,bitsize=0,type=integer)]
        struct_type = self.value_type(7,8,fields=lambda:fields)
        array_type = self.value_type(6,288*8,target=lambda:struct_type,range=lambda:(0,287))
        class Value:
            is_optimized_out = False
            def __init__(self, t, address, children=None):
                self.type, self.address, self.children = t, address, children
            def __getitem__(self, key):
                return self.children[key]
            def format_string(self, **kwargs):
                return '126'
            def __int__(self):
                return 26
        rows = [Value(struct_type,0x100+i*8,{'member':Value(integer,0x100+i*8)}) for i in range(288)]
        array = Value(array_type,0x100,rows)
        data = ['00']*(288*8)
        data[7*8+4] = 'ff'  # An unused member of an earlier element is undefined.
        calls = []
        def execute(command, **kwargs):
            words = command.split(); address, size = int(words[-2],16), int(words[-1])
            calls.append((address,size))
            if address == 0x2000:
                return '00'*size
            return ''.join(data[address-0x100:address-0x100+size])
        self.gdb.execute = execute
        frame = SimpleNamespace(read_var=lambda name:array)
        result = self.scope['check_source_expressions'](frame,{'identifiers':'return arr[26].member;'}, {'queries':256,'bytes':32768})
        row = result['items'][0]
        self.assertEqual(row['value'], '126')
        self.assertEqual(row['initialization']['status'], 'defined')
        prefix = row['index_accesses'][0]['prefix_check']
        self.assertEqual(prefix['checked_elements'], 27)
        self.assertEqual(prefix['checked_bytes'], 108)
        self.assertEqual(prefix['member_path'], 'member')
        self.assertEqual(prefix['ranges'], [{'start':0,'end':26,'status':'defined'}])
        self.assertTrue(all(size == 4 and (address-0x100)%8 == 0 for address,size in calls))
        self.assertIn((0x100+26*8,4), calls)
        index_value = Value(integer, 0x2000)
        context = Value(struct_type,0x2000,{'index':index_value})
        owner = Value(struct_type,0x2000,{'ru':array,'index':index_value})
        st = Value(self.value_type(4,8,target=lambda:struct_type),0x2000)
        st.dereference = lambda:owner
        variable_frame = SimpleNamespace(read_var=lambda name:{'arr':array,'index':index_value,'ctx':context,'st':st}[name])
        for expression in ('arr[index].member','arr[ctx.index].member','arr[index + 0].member',
                           'st->ru[26].member','st->ru[index].member','st->ru[st->index].member'):
            with self.subTest(expression=expression):
                actual = self.scope['check_source_expressions'](variable_frame,{'identifiers':'return '+expression+';'}, {'queries':256,'bytes':32768})['items'][0]
                access = actual['index_accesses'][0]
                self.assertEqual(access['actual_index'],26)
                self.assertEqual(access['prefix_check']['requested'],[0,26])
                self.assertEqual(access['prefix_check']['checked_elements'],27)
                self.assertEqual(access['prefix_check']['checked_bytes'],108)
        class PointerValue(Value):
            def __int__(self):
                return 0x100
        pointer = PointerValue(self.value_type(4,8,target=lambda:struct_type),0x2000,rows)
        owner.children['ru'] = pointer
        dynamic = self.scope['check_source_expressions'](variable_frame,{'identifiers':'return st->ru[index].member;'}, {'queries':256,'bytes':32768})['items'][0]
        self.assertEqual(dynamic['index_accesses'][0]['actual_index'],26)
        self.assertIsNone(dynamic['index_accesses'][0]['bounds'])
        probe = dynamic['index_accesses'][0]['prefix_check']
        self.assertEqual(probe['range_basis'],'index_probe')
        self.assertIsNone(probe['bounds'])
        self.assertEqual(probe['checked_elements'],27)
        self.assertEqual(probe['checked_bytes'],108)
        self.assertEqual(dynamic['status'],'available')
        # An unaddressable selected member is never read through GDB.Value.
        data[26*8:26*8+4] = ['__']*4
        pointer.children = {}  # Any indexing now raises; the probe must avoid it.
        bad = self.scope['check_source_expressions'](variable_frame,{'identifiers':'return st->ru[index].member;'}, {'queries':256,'bytes':32768})['items'][0]
        self.assertIn('不读取其值',bad['value'])
        self.assertEqual(bad['index_accesses'][0]['prefix_check']['ranges'][-1], {'start':26,'end':26,'status':'unaddressable'})
        data[26*8:26*8+4] = ['00']*4
        def allocation_execute(command, **kwargs):
            if command.startswith('monitor v.info location '):
                address = int(command.split()[-1],16)
                start = 0x100 if address == 0x100 else 0x1d0
                return "Address %s is %d bytes inside a block of size 8 alloc'd" % (hex(address),address-start)
            return execute(command,**kwargs)
        self.gdb.execute = allocation_execute
        outside = self.scope['check_source_expressions'](variable_frame,{'identifiers':'return st->ru[index].member;'}, {'queries':256,'bytes':32768})['items'][0]
        self.assertIn('越过原分配块',outside['value'])
        self.assertEqual(outside['index_accesses'][0]['prefix_check']['allocation']['status'],'outside_block')
        owner.children['ru'] = array
        array_type.pointer = lambda:self.value_type(4,8)
        for last in (0,-1):
            array_type.range = lambda last=last:(0,last)
            tail = self.scope['check_source_expressions'](variable_frame,{'identifiers':'return st->ru[index].member;'}, {'queries':256,'bytes':32768})['items'][0]
            access = tail['index_accesses'][0]
            self.assertEqual(access['actual_index'],26)
            self.assertEqual(access['bounds'],[0,last])
            self.assertEqual(access['prefix_check']['checked_elements'],27)
            self.assertEqual(access['prefix_check']['checked_bytes'],108)
            self.assertEqual(access['prefix_check']['range_basis'],'tail_array_probe')
            self.assertEqual(tail['initialization']['status'],'out_of_bounds')
            self.assertEqual(tail['status'],'unavailable')
        array_type.range = lambda:(0,287)
        data[7*8] = 'ff'
        result = self.scope['check_source_expressions'](frame,{'identifiers':'return arr[26].member;'}, {'queries':256,'bytes':32768})
        self.assertIn({'start':7,'end':7,'status':'undefined'}, result['items'][0]['index_accesses'][0]['prefix_check']['ranges'])

    def test_nested_member_offsets_and_cache_are_member_specific(self):
        integer = self.value_type()
        inner = self.value_type(7,8,fields=lambda:[SimpleNamespace(name='x',bitpos=32,bitsize=0,type=integer)])
        outer = self.value_type(7,16,fields=lambda:[SimpleNamespace(name='nested',bitpos=64,bitsize=0,type=inner),
                                                 SimpleNamespace(name='other',bitpos=0,bitsize=0,type=integer)])
        raw = ['ff']*48
        for i in range(3): raw[i*16+12:i*16+16] = ['00']*4
        budget = {}
        result,calls = self.array_prefix(raw,2,2,outer,budget,('nested','x'))
        self.assertEqual(result['ranges'],[{'start':0,'end':2,'status':'defined'}])
        self.assertEqual(calls,[(0x10c,4),(0x11c,4),(0x12c,4)])
        other,calls = self.array_prefix(raw,2,2,outer,budget,('other',))
        self.assertEqual(other['ranges'],[{'start':0,'end':2,'status':'undefined'}])
        self.assertEqual(len(calls),3)

    def test_dynamic_pointer_null_unknown_and_overflow_do_not_probe_memory(self):
        pointer_type = self.value_type(4,8,target=lambda:self.value_type())
        class Pointer:
            type = pointer_type
            address = 0x2000
            def __init__(self, value): self.value = value
            def __int__(self): return self.value
        for value, bits, expected in ((0,'00','空指针'),(0x100,'ff','未确认完整初始化'),((1<<64)-2,'00','超出目标指针地址范围')):
            with self.subTest(value=value,bits=bits):
                calls = []
                def execute(command, **kwargs):
                    calls.append(command)
                    return bits*8
                self.gdb.execute = execute
                result = self.scope['check_array_prefix'](Pointer(value),26,0,26,{'queries':256,'bytes':32768},dynamic=True)
                self.assertEqual(len(calls),1)  # Only the pointer's own initialized storage.
                self.assertIn('0x2000 8',calls[0])
                self.assertEqual(result['checked_elements'],0)
                self.assertIn(expected,result['reason'])
                self.assertIsNone(result['bounds'])

    def test_large_struct_prefix_is_chunked_and_reused(self):
        fields = [SimpleNamespace(name='member',bitpos=0,bitsize=0,type=self.value_type())]
        element = self.value_type(7,8192,fields=lambda:fields)
        budget = {}
        result, calls = self.array_prefix(['00']*(8192*27),26,287,element,budget)
        self.assertEqual(result['checked_elements'],27)
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(len(calls),54)
        self.assertTrue(all(size <= 4096 for _,size in calls))
        remaining = dict(budget['array_prefix'])
        reused, calls = self.array_prefix([],26,287,element,budget)
        self.assertTrue(reused['reused'])
        self.assertEqual(calls,[])
        self.assertEqual(reused['checked_bytes'],8192*27)
        self.assertEqual(remaining,budget['array_prefix'])
        partial, _ = self.array_prefix(['00']*(8192*27),26,287,element,
                                      {'array_prefix':{'queries':3,'bytes':1048576,'ranges':512}})
        self.assertEqual(partial['checked_elements'],1)
        self.assertEqual(partial['checked_bytes'],8192)
        self.assertEqual(partial['queried_bytes'],12288)
        self.assertFalse(partial['coverage_complete'])
        self.assertEqual(partial['ranges'][-1],{'start':1,'end':26,'status':'unchecked'})
        self.assertIn('查询预算已耗尽',partial['reason'])

    def test_source_line_beyond_one_mib_with_bounded_window(self):
        source = self.root / 'large.c'
        prefix = '/* ' + 'padding ' * 30 + '*/\n'
        source.write_text(prefix * 5000 + 'for (int i = 0;\n i < limit;\n ++i) {\n use(value);\n}\n', encoding='utf-8')
        self.assertGreater(source.stat().st_size, 1024 * 1024)
        read = self.scope['capture_source_line']
        result = read(str(source), 5002)
        self.assertEqual(result['text'], ' i < limit;')
        self.assertIn('limit', result['identifiers'])
        header = result['control_header']
        self.assertEqual((header['start_line'], header['end_line']), (5001, 5003))
        self.assertEqual([r['number'] for r in header['lines']], [5001, 5002, 5003])
        with patch('builtins.open', side_effect=AssertionError('cached source should not be reread')):
            self.assertEqual(read(str(source), 5002), result)
        result['text'] = 'mutated'
        self.assertEqual(read(str(source), 5002)['text'], ' i < limit;')
        missing = read(str(source), 6000)
        self.assertIn('实际只有 5005 行', missing['note'])
        self.assertNotIn('1 MiB', missing['note'])

    def test_streamed_source_preserves_comment_state_before_window(self):
        source = self.root / 'comment.c'
        source.write_text('/*\n' + 'ignored_name\n' * 150 + '*/ use(value, "/* fake */"); // hidden\n', encoding='utf-8')
        read = self.scope['capture_source_line']
        self.assertEqual(read(str(source), 150)['identifiers'], '')
        result = read(str(source), 152)
        self.assertIn('use(value,', result['identifiers'])
        self.assertNotIn('fake', result['identifiers'])
        self.assertNotIn('hidden', result['identifiers'])
        source.write_text('return updated_value;\n', encoding='utf-8')
        self.assertIn('updated_value', read(str(source), 1)['identifiers'])

    def test_multiline_control_header_excludes_body_and_comments(self):
        source = self.root/'controls.c'
        source.write_text('for (int i = 0;\n i < options->limit; /* ignored_name */\n ++i) {\n use(body_only);\n}\nswitch (\n options\n ->mode) {\n case 1: use(case_only);\n}\n', encoding='utf-8')
        read = self.scope['capture_source_line']
        header = read(str(source),2)['control_header']
        self.assertEqual((header['start_line'], header['end_line']), (1,3))
        self.assertIn('options->limit', header['identifiers'])
        self.assertNotIn('ignored_name', header['identifiers'])
        self.assertNotIn('body_only', header['identifiers'])
        self.assertNotIn('control_header', read(str(source),4))
        self.assertEqual(read(str(source),7)['control_header']['start_line'],6)
        self.assertNotIn('control_header', read(str(source),9))

    def test_source_path_mapping_reads_exact_file_without_basename_guess(self):
        source = self.root / 'mapped.c'
        source.write_text('return mapped_value;\n', encoding='utf-8')
        errors = [{'stacks':[{'frames':[{'dir':'/old/build','file':'mapped.c','local_file':str(source)}]}]}]
        _, script = av.prepare_capture(self.root/'mapping', 'id', source_errors=errors)
        config = {}
        exec(script.read_text(encoding='utf-8').splitlines()[0], config)
        self.scope['_capture_config'].update(config['_capture_config'])
        read = self.scope['capture_source_line']
        self.assertEqual(read('/old/build/mapped.c', 1)['text'], 'return mapped_value;')
        self.assertNotIn('text', read('/another/build/mapped.c', 1))

    def test_expression_failure_does_not_skip_scope_variables(self):
        def fail(*args):
            raise RuntimeError('failed source inspection')
        self.scope['check_source_expressions'] = fail
        self.count = 1
        self.collector.on_stop(None)
        data = json.loads((self.root/'error-0001.json').read_text(encoding='utf-8'))
        row = data['frames'][0]
        self.assertTrue(any(v['status'] == 'available' for v in row['variables']))
        self.assertIn('failed source inspection', str(row['inspection_errors']))

    def test_post_read_failure_preserves_scope_value(self):
        def fail(*args):
            raise RuntimeError('member layout unavailable')
        self.scope['describe_members'] = fail
        rows, _ = self.scope['capture_variables'](self.frame)
        value = next(v for v in rows if v['name'] == 'i')
        self.assertEqual(value['status'], 'available')
        self.assertEqual(value['value'], '4')
        self.assertEqual(value['initialization']['status'], 'unknown')

    def test_runtime_error_categories_use_same_collector(self):
        messages = ['Invalid read of size 4', 'Invalid write of size 4',
                    'Conditional jump or move depends on uninitialised value(s)',
                    'Use of uninitialised value of size 8',
                    'Invalid free() / delete / delete[] / realloc()',
                    'Syscall param write(buf) points to uninitialised byte(s)']
        for number, message in enumerate(messages, 1):
            self.count = number
            self.error = message + '\n at 0x100: bad (demo.c:9)'
            self.collector.on_stop(None)
            data = json.loads((self.root/('error-%04d.json'%number)).read_text(encoding='utf-8'))
            self.assertEqual(data['association']['status'], 'verified')
            self.assertTrue(any(v['status']=='available' for v in data['frames'][0]['variables']))

    def test_inline_frame_does_not_collect_enclosing_function_locals(self):
        class Block(list):
            is_global=False
            is_static=False
            function=None
            superblock=None
        symbol=lambda name: SimpleNamespace(name=name,is_argument=False,is_variable=True)
        enclosing=Block([symbol('html')]);enclosing.function=SimpleNamespace(name='caller')
        function=Block([symbol('gone')]);function.function=SimpleNamespace(name='inline_function');function.superblock=enclosing
        inner=Block([symbol('i')]);inner.superblock=function
        self.frame.block=lambda:inner
        values,note=self.scope['capture_variables'](self.frame)
        self.assertEqual([row['name'] for row in values],['i','gone'])
        self.assertNotIn('html',[row['name'] for row in values])

    def test_capture_records_pc_mismatch_as_unverified(self):
        self.frame.pc=lambda:0x999
        self.count=1
        self.collector.on_stop(None)
        data=json.loads((self.root/'error-0001.json').read_text(encoding='utf-8'))
        self.assertEqual(data['association']['status'],'unverified')
        self.assertEqual(data['association']['diagnostic_pc'],'0x100')
        self.assertEqual(data['association']['current_pc'],'0x999')

    def test_capture_verifies_current_pc(self):
        self.frame.pc=lambda:0x100
        self.count=1
        self.collector.on_stop(None)
        data=json.loads((self.root/'error-0001.json').read_text(encoding='utf-8'))
        self.assertEqual(data['association']['status'],'verified')

    def test_source_expression_checks_are_saved_in_the_capture(self):
        from tests.test_line_checks import Value
        source=self.root/'source.c'
        source.write_text('return target.used;\n', encoding='utf-8')
        self.frame.find_sal=lambda:SimpleNamespace(symtab=SimpleNamespace(fullname=lambda:str(source)),line=1)
        target=Value({'used':Value(73), 'unrelated':Value(999)}, code=self.gdb.TYPE_CODE_STRUCT)
        read=self.frame.read_var
        self.frame.read_var=lambda symbol:target if symbol=='target' else read(symbol)
        self.count=1
        self.collector.on_stop(None)
        data=json.loads((self.root/'error-0001.json').read_text(encoding='utf-8'))
        checks=data['frames'][0]['line_checks']['items']
        self.assertEqual([(v['name'],v['value']) for v in checks],[('target.used','73')])
        self.assertNotIn('unrelated', str(checks))

    def test_unverified_first_capture_does_not_suppress_verified_repeat(self):
        self.frame.pc=lambda:0x999
        self.count=1
        self.collector.on_stop(None)
        self.frame.pc=lambda:0x100
        self.count=2
        self.collector.on_stop(None)
        data=json.loads((self.root/'error-0002.json').read_text(encoding='utf-8'))
        self.assertEqual(data['association']['status'],'verified')

    def test_more_than_twenty_stops_with_duplicate_error_count_gaps(self):
        original=self.execute
        for n in range(1,41):
            self.count=n*100
            self.gdb.execute=lambda command,**kw: ('n_errs_found %d n_errs_shown %d' % (self.count,n)
                if command=='monitor v.info n_errs_found' else original(command,**kw))
            self.error='Invalid write of size 4\n at 0x100: bad (demo.c:%d)' % n
            self.collector.on_stop(None)
            data=json.loads((self.root/('error-%04d.json'%n)).read_text(encoding='utf-8'))
            self.assertEqual(data['association']['status'],'verified')
            self.assertTrue(self.collector.resume_allowed)
        self.assertEqual(self.collector.sequence,40)

    def test_ambiguous_count_gap_still_not_verified(self):
        self.count=100
        self.collector.on_stop(None)
        data=json.loads((self.root/'error-0001.json').read_text(encoding='utf-8'))
        self.assertEqual(data['association']['status'],'unverified')
        self.assertTrue(any('本次现场未关联' in text for text in self.messages))

    def test_distinct_instructions_on_same_source_line_are_captured_separately(self):
        self.count=1
        self.collector.on_stop(None)
        self.error=self.error.replace('0x100','0x104')
        self.frame.pc=lambda:0x104
        self.count=2
        self.collector.on_stop(None)
        data=json.loads((self.root/'error-0002.json').read_text(encoding='utf-8'))
        self.assertEqual(data['association']['status'],'verified')
        self.assertEqual(data['association']['current_pc'],'0x104')

    def test_struct_member_values_are_bounded_and_do_not_follow_pointers(self):
        class Value:
            is_optimized_out = False
            def __init__(self, data, code):
                self.data = data
                self.type = SimpleNamespace(code=code)
                self.type.strip_typedefs = lambda: self.type
                self.type.fields = lambda: [SimpleNamespace(name=n, bitpos=0, type='int') for n in data]
                self.type.range = lambda: (0, len(data)-1)
            def __getitem__(self, key):
                if self.type.code == 4:
                    raise AssertionError('must not dereference pointers')
                return self.data[key]
            def format_string(self, **kwargs):
                return str(self.data)
        obj = Value({'count': Value(7, 1), 'nested': Value({'limit': Value(99, 1)}, 7),
                     'ptr': Value('0x1234', 4), 'items': Value([Value(i, 1) for i in range(100)], 6)}, 7)
        info = {'member_states': [{'name': 'nested.limit', 'status': 'undefined'}]}
        result = self.scope['describe_members'](obj, info)
        rows = {row['name']: row for row in result['members']}
        self.assertEqual(rows['count']['value'], '7')
        self.assertEqual(rows['nested.limit']['initialization'], 'undefined')
        self.assertIn('未读取', rows['ptr']['note'])
        self.assertIn('items[15]', rows)
        self.assertNotIn('items[16]', rows)
        self.assertIn('16', result['note'])

    def test_memory_access_allowed_and_actual_ranges(self):
        facts = self.scope['memory_facts']
        for relation, distance, expected in [('after',0,40),('before',4,-4),('inside',38,38)]:
            result = facts('Invalid write of size 4\nAddress 0x1234 is %d bytes %s a block of size 40 alloc\'d' % (distance,relation))
            self.assertEqual(result['valid_start_offset_bytes'],[0,36])
            self.assertEqual(result['actual_offset_bytes'],expected)
            self.assertEqual(result['actual_last_offset_bytes'],expected+3)
        freed = facts('Invalid read of size 4\nAddress 0x1234 is 0 bytes inside a block of size 40 free\'d')
        self.assertIsNone(freed['valid_start_offset_bytes'])
        self.assertNotIn('valid_start_offset_bytes', facts('Invalid read of size 4'))
        boundary=facts('Invalid read of size 4\nAddress 0x1010 is 0 bytes after a block of size 16 alloc\'d')
        self.assertEqual(boundary['valid_start_addresses'],['0x1000','0x100c'])
        unknown=facts('Invalid write of size 8\nAddress 0x0 is not stack\'d, malloc\'d or free\'d')
        self.assertIn('合法范围未知',unknown['range_explanation'])
        self.assertIn('0x0',unknown['range_explanation'])
        unrelated=facts('Invalid write of size 1\nAddress 0x0 is not stack\'d\nAddress 0x300 is 0 bytes inside a block of size 288 alloc\'d')
        self.assertNotIn('block_bytes', unrelated)
        self.assertNotIn('287', unrelated['range_explanation'])
        evidence=facts('Invalid write of size 1\nAddress 0x300 is 0 bytes after a block of size 288 alloc\'d')
        self.assertEqual(evidence['range_evidence_version'], 2)
        self.assertIn('尚未确认对应哪个变量', evidence['range_explanation'])
        self.assertIn('Address 0x300', evidence['range_evidence'])

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
                                     pc=lambda:0x100,
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
        self.error = self.error.replace('0x200', '0x900')
        self.collector.on_stop(None)
        self.collector.on_stop(None)  # a later breakpoint with unchanged counter
        self.assertEqual(len(list(self.root.glob('*.json'))), 1)
        self.assertEqual((self.root / 'error-0001.json').read_bytes(), first)
        self.count = 3
        self.error = self.error.replace('demo.c:9', 'demo.c:10')
        self.collector.on_stop(None)
        self.assertEqual(len(list(self.root.glob('*.json'))), 2)

    def automatic_run(self, stops, monitor=None):
        self.scope['_capture_config']['auto_continue'] = True
        self.gdb.GdbError = RuntimeError
        running = [True]
        pending = iter(stops)
        self.continues = 0
        self.gdb.selected_inferior = lambda: SimpleNamespace(threads=lambda: [1] if running[0] else [])
        def execute(command, **kwargs):
            if command != 'continue':
                return (monitor or self.execute)(command, **kwargs)
            self.continues += 1
            self.assertLess(self.continues, 10, 'must not loop on an unknown stop')
            stop = next(pending, None)
            if stop is None:
                running[0] = False
            else:
                self.count, event, line = stop
                self.error = self.error.replace('demo.c:9', 'demo.c:' + str(line))
                self.collector.on_stop(event)
        self.gdb.execute = execute
        self.collector.run_to_completion()

    def test_automatic_capture_continues_through_unique_and_duplicate_errors(self):
        event = SimpleNamespace(stop_signal='SIGTRAP')
        self.automatic_run([(1, event, 9), (2, event, 9), (3, event, 10)])
        self.assertEqual(self.continues, 4)
        self.assertEqual(len(list(self.root.glob('error-*.json'))), 2)
        self.assertFalse(any('现场保持暂停' in msg for msg in self.messages))
        self.assertTrue(any('自动采集完成' in msg for msg in self.messages))

    def test_automatic_replay_without_errors_exits(self):
        self.automatic_run([])
        self.assertEqual(self.continues, 1)
        self.assertFalse(list(self.root.glob('error-*.json')))

    def test_automatic_does_not_swallow_signals_or_breakpoints(self):
        for event in (SimpleNamespace(stop_signal='SIGINT'), SimpleNamespace(stop_signal='SIGSEGV'),
                      SimpleNamespace(breakpoints=[object()])):
            with self.subTest(event=event), self.assertRaisesRegex(RuntimeError, '自动分析已停止'):
                self.automatic_run([(1, event, 9)])
            self.assertEqual(self.continues, 1)
            self.assertFalse(list(self.root.glob('error-*.json')))

    def test_automatic_unknown_stop_does_not_loop(self):
        with self.assertRaisesRegex(RuntimeError, '自动分析已停止'):
            self.automatic_run([(0, None, 9)])
        self.assertEqual(self.continues, 1)

    def test_automatic_monitor_failure_exits_instead_of_waiting(self):
        with self.assertRaisesRegex(RuntimeError, '无法读取错误计数'):
            self.automatic_run([(1, None, 9)], monitor=lambda *args, **kwargs: 'unsupported command')
        self.assertEqual(self.continues, 1)

    def test_automatic_save_failure_is_reported(self):
        with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                self.automatic_run([(1, None, 9)])
        self.assertEqual(self.continues, 1)

    def test_automatic_disconnect_is_not_resumed(self):
        def monitor(command, **kwargs):
            raise RuntimeError('Connection reset by peer')
        with self.assertRaisesRegex(RuntimeError, 'Connection reset by peer'):
            self.automatic_run([(1, None, 9)], monitor=monitor)
        self.assertEqual(self.continues, 1)
        self.assertTrue((self.root / 'connection-error.txt').exists())

    def test_automatic_commands_use_driver_only_when_requested(self):
        _, script = av.prepare_capture(self.root / 'auto', 'test', auto_continue=True)
        self.assertIn("'auto_continue': True", script.read_text(encoding='utf-8'))
        commands = av.debug_commands({}, 42, '/tmp/test', '/usr/bin/vgdb', capture_script=script, auto_continue=True)
        self.assertEqual(commands[-1], 'python _aivalgrind_capture.run_to_completion()')
        self.assertFalse(any(command.startswith('break ') for command in commands))

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

    def test_disconnect_aborts_scan_and_records_failure_without_retry(self):
        self.count = 1
        calls = []
        original = self.execute
        def execute(command, **kwargs):
            calls.append(command)
            if 'get_vbits' in command:
                raise RuntimeError('Remote communication error. Target disconnected: Connection reset by peer')
            return original(command, **kwargs)
        self.gdb.execute = execute
        self.frame.read_var = lambda symbol: SimpleNamespace(type=self.value_type(), address=0x100,
            is_optimized_out=False, format_string=lambda **kwargs: '0')
        self.collector.on_stop(None)
        self.assertEqual(sum('get_vbits' in c for c in calls), 1)
        failure = self.root / 'connection-error.txt'
        self.assertIn('Connection reset by peer', failure.read_text(encoding='utf-8'))
        self.assertFalse(list(self.root.glob('error-*.json')))
        self.assertFalse(self.collector.active)
        self.collector.on_stop(None)
        self.assertEqual(sum('get_vbits' in c for c in calls), 1)

    def test_empty_line_capture_retries_until_readable(self):
        checks = {'version': 1, 'items': [{'name': 'i', 'value': 'unavailable', 'status': 'unavailable', 'initialization': {'status': 'unknown'}}]}
        self.scope['check_source_expressions'] = lambda *args: checks
        self.count = 1
        self.collector.on_stop(None)
        first = json.loads((self.root / 'error-0001.json').read_text(encoding='utf-8'))
        self.assertTrue(first['retry_on_repeat'])
        self.assertFalse(self.collector.seen)
        checks['items'][0]['status'] = 'available'
        self.count = 2
        self.collector.on_stop(None)
        second = json.loads((self.root / 'error-0002.json').read_text(encoding='utf-8'))
        self.assertFalse(second['retry_on_repeat'])
        self.assertTrue(self.collector.seen)
        self.count = 3
        self.collector.on_stop(None)
        self.assertEqual(len(list(self.root.glob('error-*.json'))), 2)

    def test_empty_line_capture_retries_are_bounded(self):
        self.scope['check_source_expressions'] = lambda *args: {'version': 1, 'items': [{'name': 'i', 'value': 'unavailable', 'status': 'unavailable', 'initialization': {'status': 'unknown'}}]}
        for count in range(1, 6):
            self.count = count
            self.collector.on_stop(None)
        self.assertEqual(len(list(self.root.glob('error-*.json'))), 3)
        last = json.loads((self.root / 'error-0003.json').read_text(encoding='utf-8'))
        self.assertEqual(last['empty_capture_attempt'], 3)
        self.assertFalse(last['retry_on_repeat'])

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

    def test_float_double_and_typedef_initialization(self):
        self.gdb.TYPE_CODE_FLT = 9
        for size in (4, 8):
            floating = self.value_type(9, size)
            alias = SimpleNamespace(sizeof=size, strip_typedefs=lambda: floating)
            self.assertEqual(self.inspect_init('00' * size, alias)['status'], 'defined')
            self.assertEqual(self.inspect_init('ff' * size, alias)['status'], 'undefined')
            partial = self.inspect_init('00' * (size - 1) + 'ff', alias)
            self.assertEqual(partial['status'], 'partially_undefined')
            self.assertEqual(partial['undefined_byte_offsets'], [size - 1])
            self.assertEqual(partial['member_states'][0]['name'], '$self')
        unknown = self.inspect_init('00' * 16, self.value_type(9, 16))
        self.assertEqual(unknown['status'], 'unknown')
        self.assertIn('填充字节', unknown['reason'])

    def test_double_members_and_array_exclude_structure_padding(self):
        self.gdb.TYPE_CODE_FLT = 9
        double = self.value_type(9, 8)
        fields = [SimpleNamespace(name='flag', bitpos=0, bitsize=0, type=self.value_type(5, 1)),
                  SimpleNamespace(name='ratio', bitpos=64, bitsize=0, type=double)]
        result = self.inspect_init('00' + 'ff' * 7 + '00' * 8, self.value_type(7, 16, fields=lambda: fields))
        self.assertEqual(result['status'], 'defined')
        self.assertEqual(result['ignored_padding_byte_offsets'], list(range(1, 8)))
        self.assertEqual(result['member_states'][1]['name'], 'ratio')
        array = self.value_type(6, 16, target=lambda: double, range=lambda: (0, 1))
        result = self.inspect_init('00' * 8 + 'ff' * 8, array)
        self.assertEqual(result['status'], 'partially_undefined')
        self.assertEqual(result['member_states'][1]['status'], 'undefined')
        self.assertEqual(result['undefined_byte_offsets'], list(range(8, 16)))

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

    def test_same_line_scalars_members_and_comments(self):
        source = self.root / 'line.c'
        source.write_text('/* ignored\n a */\nresult = a + b * item.used; // unused\n', encoding='utf-8')
        read_line = self.scope['capture_source_line']
        line = read_line(str(source), 3)
        self.assertIn('result = a', line['text'])
        self.assertNotIn('unused', line['identifiers'])
        fields = [SimpleNamespace(name='used', bitpos=0, bitsize=0, type=self.value_type()),
                  SimpleNamespace(name='unused', bitpos=32, bitsize=0, type=self.value_type())]
        structure = self.inspect_init('00000000ffffffff', self.value_type(7, 8, fields=lambda: fields))
        good = self.inspect_init('00000000')
        bad = self.inspect_init('ffffffff')
        frame = {'index': 0, 'source_line': line, 'variables': [
            {'name': 'a', 'initialization': good}, {'name': 'b', 'initialization': bad},
            {'name': 'item', 'initialization': structure}]}
        snapshot = {'valgrind_error': 'Conditional jump depends on uninitialised value(s)', 'frames': [frame]}
        analysis = self.scope['analyze_initialization_use'](snapshot)
        states = {row['name']: row['status'] for row in analysis['line_variables']}
        self.assertEqual(states, {'a': 'defined', 'b': 'undefined', 'item.used': 'defined'})
        self.assertTrue(all(f['severity'] == 'warning' for f in analysis['findings']))
        self.assertIn('item.unused', [f['name'] for f in analysis['findings']])
        self.assertIn('note', read_line(str(source), 100))
        self.assertIn('note', read_line(str(self.root / 'missing.c'), 1))
        frame['source_line']['identifiers'] = 'printf("b");'
        source.write_text('printf("b"); /* a */', encoding='utf-8')
        frame['source_line'] = read_line(str(source), 1)
        self.assertEqual(self.scope['line_variable_states'](frame), [])

    def test_line_matching_distinguishes_members_from_locals(self):
        frame = {'index': 0, 'source_line': {'identifiers': 'return value + ptr -> length + ns::count + item . used;'},
                 'variables': [{'name': name, 'initialization': {'status': 'undefined'}}
                               for name in ['value', 'length', 'count', 'used', 'val']]}
        rows = self.scope['line_variable_states'](frame)
        self.assertEqual([row['name'] for row in rows], ['value'])

    def test_same_line_analysis_saved_to_all_formats(self):
        source = self.root / 'demo.c'
        source.write_text('if (i + gone) {}', encoding='utf-8')
        self.frame.find_sal = lambda: SimpleNamespace(symtab=SimpleNamespace(fullname=lambda: str(source)), line=1)
        self.count = 1
        self.collector.on_stop(None)
        data = json.loads((self.root / 'error-0001.json').read_text(encoding='utf-8'))
        rows = data['initialization_analysis']['line_variables']
        self.assertEqual([row['name'] for row in rows], ['i', 'gone'])
        self.assertTrue(all(row['status'] == 'unknown' for row in rows))
        for suffix in ('.txt', '.html'):
            self.assertIn('同一行变量 #0 gone：无法检查', (self.root / ('error-0001' + suffix)).read_text(encoding='utf-8'))
        self.assertTrue(any('同一行变量 #0 i' in message for message in self.messages))


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
