"""Read-only nested-index inspection embedded in the GDB capture script."""

INDEX_ANALYSIS_SCRIPT = r'''
import ast

def nested_index_analysis(frame, source):
    text = source.get('identifiers', '')[:4096]
    rows, expressions = [], []
    cursor = 0
    while cursor < len(text) and len(expressions) < 16:
        match = re.search(r'\b[A-Za-z_]\w*', text[cursor:])
        if not match:
            break
        start = cursor + match.start()
        end = cursor + match.end()
        has_index = False
        while end < len(text):
            pos = end
            while pos < len(text) and text[pos].isspace():
                pos += 1
            member = re.match(r'(?:\.|->)\s*[A-Za-z_]\w*', text[pos:])
            if member:
                end = pos + member.end()
            elif pos < len(text) and text[pos] == '[':
                depth, pos = 1, pos + 1
                while pos < len(text) and depth:
                    depth += (text[pos]=='[') - (text[pos]==']')
                    pos += 1
                if depth:
                    break
                end, has_index = pos, True
            else:
                break
        if has_index:
            expressions.append(text[start:end])
        cursor = max(end, start+1)

    budget = [128]
    def integer(value):
        if isinstance(value, int):
            return value
        kind = value.type.strip_typedefs().code
        if kind not in (gdb.TYPE_CODE_INT, gdb.TYPE_CODE_BOOL, gdb.TYPE_CODE_ENUM, gdb.TYPE_CODE_CHAR):
            raise ValueError('索引不是可确认的整数')
        return int(value)

    def inspect(node, expression, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 16:
            raise ValueError('达到嵌套分析预算')
        if isinstance(node, ast.Name):
            return frame.read_var(node.id)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = integer(inspect(node.operand,expression,depth+1))
            return -value if isinstance(node.op,ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult)):
            left = integer(inspect(node.left,expression,depth+1))
            right = integer(inspect(node.right,expression,depth+1))
            if max(abs(left),abs(right)) > 2**63:
                raise ValueError('整数运算范围过大')
            return left+right if isinstance(node.op,ast.Add) else left-right if isinstance(node.op,ast.Sub) else left*right
        if isinstance(node, ast.Attribute):
            value = inspect(node.value,expression,depth+1)
            if value.type.strip_typedefs().code != gdb.TYPE_CODE_STRUCT:
                raise ValueError('指针成员或非结构体成员边界未知，不自动解引用')
            return value[node.attr]
        if isinstance(node, ast.Subscript):
            row = {'expression':ast.get_source_segment(expression,node) or expression,
                   'index_expression':ast.get_source_segment(expression,node.slice) or '',
                   'actual_index':None, 'bounds':None, 'status':'unknown'}
            try:
                base = inspect(node.value,expression,depth+1)
                index = integer(inspect(node.slice,expression,depth+1))
                row['actual_index'] = index
                kind = base.type.strip_typedefs()
                if kind.code != gdb.TYPE_CODE_ARRAY:
                    raise ValueError('动态指针长度未知，不推断分配长度或解引用')
                low, high = kind.range()
                low, high = int(low), int(high)
                if high < low:
                    raise ValueError('数组边界不可用')
                row['bounds'] = [low,high]
                row['status'] = 'in_bounds' if low<=index<=high else 'out_of_bounds'
                rows.append(row)
                if row['status']=='out_of_bounds':
                    raise ValueError('该级索引越界，停止读取该元素')
                return base[index]
            except Exception as exc:
                if disconnected(str(exc)):
                    raise CaptureDisconnected(str(exc))
                row['note'] = str(exc)
                if not any(r is row for r in rows):
                    rows.append(row)
                raise
        raise ValueError('不执行函数调用、自增、赋值或其他不支持的表达式')

    for original in expressions:
        expression = original.replace('->','.')
        first_row = len(rows)
        try:
            if '++' in original or '--' in original:
                raise ValueError('不执行自增或自减')
            tree = ast.parse(expression,mode='eval')
            if any(isinstance(n,(ast.Call,ast.NamedExpr)) for n in ast.walk(tree)):
                raise ValueError('不执行函数调用或赋值')
            inspect(tree.body,expression)
        except Exception as exc:
            if disconnected(str(exc)):
                raise CaptureDisconnected(str(exc))
            if not any(row['expression']==expression for row in rows[first_row:]):
                rows.append({'expression':original,'status':'unknown','note':str(exc),
                             'actual_index':None,'bounds':None})
    return {'levels':rows, 'note':'源码现场推导，不保证表达式已执行或是本次错误原因；固定数组边界来自调试类型，动态指针边界未知。'}
'''
