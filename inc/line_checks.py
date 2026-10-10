"""Bounded, read-only checks of expressions on the stopped source line."""

LINE_CHECK_SCRIPT = r'''
import ast

def check_source_expressions(frame, source, budget):
    text = source.get('identifiers', '')[:4096]
    keywords = set(('return if else for while do switch case break continue const volatile static extern '
                    'int long short signed unsigned float double char bool void struct class enum union '
                    'auto sizeof alignof typedef using namespace template typename new delete throw try catch '
                    'true false nullptr NULL static_cast reinterpret_cast dynamic_cast const_cast').split())
    expressions, seen = [], set()
    for match in re.finditer(r'\b[A-Za-z_]\w*', text):
        if match.group() in keywords or re.search(r'(?:\.|->|::)\s*$', text[:match.start()]):
            continue
        end = match.end()
        while end < len(text):
            pos = end
            while pos < len(text) and text[pos].isspace():
                pos += 1
            member = re.match(r'(?:\.|->|::)\s*[A-Za-z_]\w*', text[pos:])
            if member:
                end = pos + member.end()
            elif pos < len(text) and text[pos] == '[':
                depth, pos = 1, pos + 1
                while pos < len(text) and depth:
                    depth += (text[pos] == '[') - (text[pos] == ']')
                    pos += 1
                if depth:
                    break
                end = pos
            else:
                break
        expression = text[match.start():end]
        # Skip callees, while continuing to inspect names in their arguments.
        if text[end:].lstrip().startswith('('):
            continue
        key = re.sub(r'\s+', '', expression)
        if key not in seen:
            seen.add(key)
            expressions.append(expression)

    results = []
    for expression in expressions[:32]:
        row = {'name': expression, 'role': 'source_expression', 'status': 'unavailable',
               'value': '', 'initialization': {'status': 'unknown'}}
        operations = [64]
        def read(node, depth=0):
            operations[0] -= 1
            if operations[0] < 0 or depth > 12:
                raise ValueError('达到表达式读取预算')
            if isinstance(node, ast.Name):
                return frame.read_var(node.id)
            if isinstance(node, ast.Constant) and type(node.value) is int:
                return node.value
            if isinstance(node, ast.Attribute):
                value = read(node.value, depth+1)
                kind = value.type.strip_typedefs().code
                if kind == gdb.TYPE_CODE_PTR:
                    info = check_initialization(value, budget)
                    if info.get('observed_status', info['status']) in ('undefined', 'partially_undefined', 'unaddressable'):
                        raise ValueError('指针自身未初始化或不可访问，不读取其成员')
                    value = value.dereference()
                    kind = value.type.strip_typedefs().code
                if kind != gdb.TYPE_CODE_STRUCT:
                    raise ValueError('不是可直接读取的结构体成员')
                return value[node.attr]
            if isinstance(node, ast.Subscript):
                base = read(node.value, depth+1)
                index = integer(read(node.slice, depth+1))
                kind = base.type.strip_typedefs()
                if kind.code != gdb.TYPE_CODE_ARRAY:
                    raise ValueError('动态指针长度未知，未读取该索引元素')
                low, high = map(int, kind.range())
                if not low <= index <= high:
                    row['initialization'] = {'status': 'out_of_bounds'}
                    raise ValueError('实际索引 %s，合法范围 %s～%s；未读取越界元素' % (index, low, high))
                return base[index]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                number = integer(read(node.operand, depth+1))
                return -number if isinstance(node.op, ast.USub) else number
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                a, b = integer(read(node.left, depth+1)), integer(read(node.right, depth+1))
                if max(abs(a), abs(b)) > 2**63:
                    raise ValueError('索引整数运算范围过大')
                return a+b if isinstance(node.op, ast.Add) else a-b if isinstance(node.op, ast.Sub) else a*b
            raise ValueError('不执行函数调用、赋值、自增或不支持的表达式')
        def integer(value):
            if isinstance(value, int):
                return value
            if value.type.strip_typedefs().code not in (gdb.TYPE_CODE_INT, gdb.TYPE_CODE_BOOL, gdb.TYPE_CODE_ENUM, gdb.TYPE_CODE_CHAR):
                raise ValueError('索引不是整数')
            info = check_initialization(value, budget)
            if info.get('observed_status', info['status']) in ('undefined', 'partially_undefined', 'unaddressable'):
                raise ValueError('索引未初始化或不可访问，不据此读取元素')
            return int(value)
        try:
            if '++' in expression or '--' in expression:
                raise ValueError('不执行自增或自减')
            value = read(ast.parse(expression.replace('->', '.'), mode='eval').body)
            row['type'] = str(value.type)
            if value.is_optimized_out:
                row.update(status='optimized_out', value='已被编译器优化，无法读取')
            else:
                kind = value.type.strip_typedefs().code
                if kind in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION, gdb.TYPE_CODE_ARRAY):
                    row.update(status='unavailable', value='本行是整个对象，未展开未明确指定的成员')
                else:
                    row.update(status='available', value=value.format_string(raw=True)[:4096])
                    row['initialization'] = check_initialization(value, budget)
        except Exception as exc:
            if disconnected(str(exc)):
                raise CaptureDisconnected(str(exc))
            row['value'] = str(exc)
        results.append(row)
    return {'version': 1, 'items': results, 'truncated': len(expressions) > 32,
            'note': '逐项读取当前帧的源码表达式；不执行目标函数。初始化检查不等于业务取值检查，也不能单独证明本次错误原因。'}
'''
