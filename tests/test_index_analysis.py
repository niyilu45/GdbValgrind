import re
import unittest
from types import SimpleNamespace
from inc.index_analysis import INDEX_ANALYSIS_SCRIPT


class Value:
    def __init__(self, data, code=1):
        self.data, self.reads = data, []
        self.type = SimpleNamespace(code=code)
        self.type.strip_typedefs = lambda:self.type
        self.type.range = lambda:(0,len(data)-1)

    def __int__(self): return self.data

    def __getitem__(self, key):
        self.reads.append(key)
        return self.data[key]


class NestedIndexTests(unittest.TestCase):
    def setUp(self):
        gdb = SimpleNamespace(TYPE_CODE_INT=1,TYPE_CODE_BOOL=2,TYPE_CODE_ENUM=3,
                              TYPE_CODE_CHAR=5,TYPE_CODE_ARRAY=6,TYPE_CODE_STRUCT=7)
        scope = {'re':re,'gdb':gdb,'disconnected':lambda text:False}
        exec(INDEX_ANALYSIS_SCRIPT,scope)
        self.analyze = scope['nested_index_analysis']

    def run_expression(self, text, values):
        frame = SimpleNamespace(read_var=lambda name:values[name])
        return self.analyze(frame,{'identifiers':text})['levels']

    def test_struct_multidimensional_and_no_out_of_bounds_read(self):
        inner = Value([Value(0),Value(1)],6)
        outer = Value([inner],6)
        obj = Value({'items':outer},7)
        rows = self.run_expression('obj.items[i][j] = 4;',{'obj':obj,'i':Value(0),'j':Value(2)})
        self.assertEqual(rows[0]['bounds'],[0,0])
        self.assertEqual(rows[1]['actual_index'],2)
        self.assertEqual(rows[1]['status'],'out_of_bounds')
        self.assertEqual(inner.reads,[])

    def test_index_inside_index(self):
        rows = self.run_expression('a[index[k]]',{'a':Value([Value(0)]*4,6),
            'index':Value([Value(3)],6),'k':Value(0)})
        self.assertEqual([r['actual_index'] for r in rows],[0,3])
        self.assertTrue(all(r['status']=='in_bounds' for r in rows))

    def test_dynamic_pointer_and_negative_index(self):
        pointer = Value(100,4)
        rows = self.run_expression('p[i]',{'p':pointer,'i':Value(7)})
        self.assertIsNone(rows[0]['bounds'])
        self.assertEqual(rows[0]['actual_index'],7)
        self.assertEqual(pointer.reads,[])
        array = Value([Value(0)],6)
        rows = self.run_expression('a[-1]',{'a':array})
        self.assertEqual(rows[0]['status'],'out_of_bounds')
        self.assertEqual(array.reads,[])

    def test_side_effects_never_evaluated(self):
        for text in ('a[next()]','a[i++]','a[(i:=1)]'):
            rows = self.run_expression(text,{})
            self.assertTrue(rows)
            self.assertTrue(all(r['status']=='unknown' for r in rows))
