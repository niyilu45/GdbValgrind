import ast
import re
import unittest
from types import SimpleNamespace

from inc.line_checks import LINE_CHECK_SCRIPT


class Value:
    is_optimized_out = False

    def __init__(self, value, code=1, state='defined'):
        self.value, self.state = value, state
        self.type = SimpleNamespace(code=code, strip_typedefs=lambda: self.type,
                                    range=lambda: (0, len(value)-1))

    def __getitem__(self, key):
        return self.value[key]

    def __int__(self):
        return int(self.value)

    def dereference(self):
        return self.value

    def format_string(self, **kwargs):
        return str(self.value)


class LineCheckTests(unittest.TestCase):
    def test_post_read_check_failure_keeps_actual_value(self):
        def fail(*args):
            raise RuntimeError('initialization query failed')
        self.scope['check_initialization'] = fail
        row = self.run_line('return value;', {'value':Value(42)})[0]
        self.assertEqual(row['status'], 'available')
        self.assertEqual(row['value'], '42')
        self.assertEqual(row['initialization']['status'], 'unknown')
        self.assertIn('initialization query failed', row['initialization']['reason'])

    def test_struct_reference_index_and_reported_ranges(self):
        self.scope['gdb'].TYPE_CODE_REF=9
        reference=Value(None,code=9)
        reference.referenced_value=lambda:Value({'index':Value(1)},code=6)
        rows=self.run_line('return data[options.index];',{'data':Value([Value(2),Value(43)],code=8),'options':reference})
        self.assertEqual(rows[0]['value'],'43')
        self.assertEqual(rows[0]['index_accesses'][0]['actual_index'],1)
        self.assertEqual(rows[0]['index_accesses'][0]['bounds'],[0,1])
        rows=self.run_line('return data[options.index];',{'data':Value(4096,code=5),'options':reference})
        self.assertEqual(rows[0]['status'],'unavailable')
        self.assertEqual(rows[0]['index_accesses'][0]['actual_index'],1)
        self.assertIsNone(rows[0]['index_accesses'][0]['bounds'])

    def map_value(self):
        class MapType:
            code=6
            def strip_typedefs(self): return self
            def __str__(self): return 'std::map<int, int>'
            def template_argument(self,i): return 'std::less<int>'
        value=Value({},code=6);value.type=MapType()
        return value

    def test_map_printer_lookup_summary_and_missing_key_do_not_insert(self):
        value=self.map_value()
        self.scope['gdb'].default_visualizer=lambda v:SimpleNamespace(display_hint=lambda:'map',children=lambda:iter([
            ('0',Value(2)),('1',Value(29)),('2',Value(3)),('3',Value(31))]))
        rows=self.run_line('use(values[key], values);',{'values':value,'key':Value(2)})
        self.assertEqual(rows[0]['value'],'29')
        self.assertIn('2 => 29',rows[2]['value'])
        rows=self.run_line('return values[key];',{'values':value,'key':Value(99)})
        self.assertEqual(rows[0]['status'],'unavailable')
        self.assertIn('不会插入',rows[0]['value'])
        self.assertEqual(value.value,{})

    def test_native_libstdcpp_map_node_layouts_and_cycle_detection(self):
        class Pointer:
            def __init__(self,address,node): self.address,self.node=address,node
            def __int__(self): return self.address
            def cast(self,t): return self
            def dereference(self): return self.node
        zero=Pointer(0,None)
        pair={'first':Value(2),'second':Value(47)}
        node={'_M_left':zero,'_M_right':zero,'_M_value_field':pair}
        root=Pointer(1,node)
        pair_type=SimpleNamespace(pointer=lambda:None)
        tree=Value({'_M_impl':{'_M_header':{'_M_parent':root}}})
        tree.type.template_argument=lambda i:pair_type
        value=self.map_value();value.value={'_M_t':tree}
        self.scope['gdb'].lookup_type=lambda n:SimpleNamespace(pointer=lambda:None)
        lookup=self.scope['source_map_lookup']
        self.assertEqual(int(lookup(value,2)),47)
        del node['_M_value_field'];node['_M_storage']=SimpleNamespace(address=Pointer(2,pair))
        self.assertEqual(int(lookup(value,2)),47)
        node['_M_left']=root
        with self.assertRaisesRegex(ValueError,'循环'): lookup(value,2)

    def setUp(self):
        self.checked = []
        def check(value, budget):
            self.checked.append(value)
            return {'status': value.state}
        self.scope = {'re': re, 'ast': ast, 'gdb': SimpleNamespace(
            TYPE_CODE_INT=1, TYPE_CODE_BOOL=2, TYPE_CODE_ENUM=3, TYPE_CODE_CHAR=4,
            TYPE_CODE_PTR=5, TYPE_CODE_STRUCT=6, TYPE_CODE_UNION=7, TYPE_CODE_ARRAY=8),
            'check_initialization': check, 'disconnected': lambda text: False}
        exec(LINE_CHECK_SCRIPT, self.scope)

    def run_line(self, line, values):
        frame = SimpleNamespace(read_var=lambda name: values[name])
        return self.scope['check_source_expressions'](frame, {'identifiers': line}, {})['items']

    def test_reads_and_checks_exact_pointer_member_array_element_and_global(self):
        length, index, item, count = Value(13, state='undefined'), Value(1), Value(42), Value(7)
        request = Value(Value({'length': length, 'unrelated': Value(999)}, code=6), code=5)
        rows = self.run_line('use(request->length, data[i], count);',
                             {'request': request, 'data': Value([Value(0), item], code=8),
                              'i': index, 'count': count})
        actual = {r['name']: r for r in rows}
        self.assertEqual(set(actual), {'request->length', 'data[i]', 'i', 'count'})
        self.assertEqual(actual['request->length']['value'], '13')
        self.assertEqual(actual['request->length']['initialization']['status'], 'undefined')
        self.assertEqual(actual['data[i]']['value'], '42')
        self.assertTrue(all(v in self.checked for v in (length, index, item, count)))
        self.assertNotIn('unrelated', str(rows))

    def test_coverage_counts_and_limit_are_explicit(self):
        values = {'v'+str(i): Value(i) for i in range(40)}
        del values['v1']
        frame = SimpleNamespace(read_var=lambda name: values[name])
        result = self.scope['check_source_expressions'](frame, {'identifiers': 'use('+','.join('v'+str(i) for i in range(40))+');'}, {})
        self.assertTrue(result['truncated'])
        self.assertEqual(result['coverage']['recognized'], 40)
        self.assertEqual(result['coverage']['attempted'], 32)
        self.assertEqual(result['coverage']['read'], 31)
        self.assertEqual(result['coverage']['unread'], 1)

    def test_failed_read_is_an_explicit_item_not_replaced_by_other_locals(self):
        row = self.run_line('return missing;', {'unrelated': Value(99)})[0]
        self.assertEqual(row['name'], 'missing')
        self.assertEqual(row['status'], 'unavailable')
        self.assertIn('missing', row['value'])

    def test_rejects_calls_and_out_of_bounds_without_reading_element(self):
        class Array(Value):
            def __getitem__(self, key):
                raise AssertionError('must not read element')
        values = {'a': Array([Value(1)], code=8), 'i': Value(10)}
        rows = self.run_line('use(a[i], a[next()]);', values)
        self.assertEqual(rows[0]['initialization']['status'], 'out_of_bounds')
        self.assertEqual(rows[-1]['status'], 'unavailable')
        self.assertIn('不执行函数调用', rows[-1]['value'])

    def test_undefined_index_is_not_used_to_read_element(self):
        rows = self.run_line('return a[i];', {'a': Value([Value(1)], code=8), 'i': Value(0, state='undefined')})
        self.assertIn('索引未初始化', rows[0]['value'])
        self.assertEqual(rows[0]['status'], 'unavailable')
        self.assertEqual(rows[1]['initialization']['status'], 'undefined')

    def test_nested_members_use_leaf_value_and_lookup_limit(self):
        rows = self.run_line('return object.inner.field;',
                             {'object': Value({'inner': Value({'field': Value(3)}, code=6)}, code=6)})
        self.assertEqual([(r['name'], r['value']) for r in rows], [('object.inner.field', '3')])
        rows = self.run_line(' + '.join('v%d' % i for i in range(100)), {})
        self.assertEqual(len(rows), 32)

    def test_lookup_uses_exact_frame_block_for_pointer_and_scoped_enum(self):
        block = object()
        pointer = Value(Value({'abc': Value(17)}, code=6), code=5)
        enum = Value(5, code=3)
        frame = SimpleNamespace(block=lambda:block, read_var=lambda name: self.fail('string lookup should not be used'))
        def lookup(name, actual_block):
            self.assertIs(actual_block, block)
            value = {'rxvec':pointer, 'Mode::Ready':enum}[name]
            def resolve(actual_frame):
                self.assertIs(actual_frame, frame)
                return value
            return SimpleNamespace(is_variable=name=='rxvec', is_constant=name!='rxvec', value=resolve), False
        self.scope['gdb'].lookup_symbol=lookup
        rows=self.scope['check_source_expressions'](frame, {'identifiers':'use(rxvec->abc, Mode::Ready);'}, {})['items']
        self.assertEqual([(r['name'],r['value']) for r in rows],[('rxvec->abc','17'),('Mode::Ready','5')])
        self.assertEqual(rows[1]['initialization']['status'],'defined')
        self.assertNotIn(enum,self.checked)

    def test_implicit_this_member_resolves_pointer(self):
        pointer=Value(Value({'abc':Value(19)},code=6),code=5)
        owner=Value(Value({'rxvec':pointer},code=6),code=5)
        frame=SimpleNamespace(block=lambda:object(),read_var=lambda name:owner if name=='this' else None)
        self.scope['gdb'].lookup_symbol=lambda name,block:(None,True)
        rows=self.scope['check_source_expressions'](frame,{'identifiers':'return rxvec->abc;'}, {})['items']
        self.assertEqual(rows[0]['value'],'19')

    def test_macro_expansion_uses_frame_and_restores_selection_even_on_failure(self):
        selected=['original']
        previous=SimpleNamespace(select=lambda:selected.__setitem__(0,'original'))
        frame=SimpleNamespace(select=lambda:selected.__setitem__(0,'target'),read_var=lambda name: (_ for _ in ()).throw(ValueError(name)))
        self.scope['gdb'].selected_frame=lambda:previous
        def execute(command,**kwargs):
            self.assertEqual(selected[0],'target')
            return 'expands to: '+{'macro expand LIMIT':'(1U << 4) | 3UL',
                                  'macro expand UNSAFE':'invoke_target()'}[command]
        self.scope['gdb'].execute=execute
        rows=self.scope['check_source_expressions'](frame,{'identifiers':'use(LIMIT, UNSAFE);'}, {})['items']
        self.assertEqual(rows[0]['value'],'19')
        self.assertEqual(rows[0]['macro_expansion'],'(1U << 4) | 3UL')
        self.assertEqual(rows[1]['status'],'unavailable')
        self.assertIn('不执行函数调用',rows[1]['value'])
        self.assertEqual(selected[0],'original')

    def test_macro_without_debug_info_explains_missing_value(self):
        rows=self.run_line('return MISSING_MACRO;',{})
        self.assertEqual(rows[0]['status'],'unavailable')
        self.assertIn('-g3',rows[0]['value'])

    def test_failed_block_lookup_falls_back_to_same_frame_name(self):
        def no_block():
            raise RuntimeError('block unavailable')
        frame=SimpleNamespace(block=no_block,read_var=lambda name:Value(23))
        self.scope['gdb'].lookup_symbol=lambda *args:None
        rows=self.scope['check_source_expressions'](frame,{'identifiers':'return rxvec;'}, {})['items']
        self.assertEqual(rows[0]['value'],'23')

    def test_symbol_value_failure_uses_same_frame_symbol(self):
        def fail(frame):
            raise RuntimeError('not found via Symbol.value')
        symbol=SimpleNamespace(is_variable=True,value=fail)
        self.scope['gdb'].lookup_symbol=lambda name,block:(symbol,False)
        def read(actual):
            self.assertIs(actual,symbol)
            return Value(29)
        frame=SimpleNamespace(block=lambda:object(),read_var=read)
        rows=self.scope['check_source_expressions'](frame,{'identifiers':'return rxvec;'}, {})['items']
        self.assertEqual(rows[0]['value'],'29')
