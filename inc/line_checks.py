"""Bounded, read-only checks of expressions on the stopped source line."""

LINE_CHECK_SCRIPT = r'''
import ast

def check_source_expressions(frame, source, budget):
    previous = None
    selected = False
    try:
        if hasattr(gdb, 'selected_frame') and hasattr(frame, 'select'):
            previous = gdb.selected_frame()
            frame.select()
            selected = True
        return _check_source_expressions(frame, source, budget, selected)
    finally:
        if previous is not None:
            previous.select()


def _check_source_expressions(frame, source, budget, macro_scope=False):
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
        aliases, constants, uncertain_operands = {}, [], []
        def parse(text):
            # Python AST is only a read-only grammar, never eval/parse_and_eval.
            # Preserve scoped C++ names as single names for GDB symbol lookup.
            def qualified(m):
                key = '__aiv_scoped_%d' % len(aliases)
                while key in text or key in aliases:
                    key += '_'
                aliases[key] = re.sub(r'\s+', '', m.group())
                return key
            text = re.sub(r'\b[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)+', qualified, text)
            text = re.sub(r'\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b', r'\1', text)
            return ast.parse(text.replace('->', '.'), mode='eval').body

        def name_value(name):
            name = aliases.get(name, name)
            if hasattr(gdb, 'lookup_symbol'):
                symbol, implicit = gdb.lookup_symbol(name, frame.block())
                if implicit:
                    owner = frame.read_var('this')
                    if owner.type.strip_typedefs().code == gdb.TYPE_CODE_PTR:
                        owner = owner.dereference()
                    return owner[name]
                if symbol is not None:
                    if not any(getattr(symbol, flag, False) for flag in ('is_variable', 'is_argument', 'is_constant')):
                        raise ValueError('该名称不是变量或枚举常量：' + name)
                    value = symbol.value(frame)
                    if getattr(symbol, 'is_constant', False):
                        constants.append(value)
                    return value
            try:
                return frame.read_var(name)
            except Exception as exc:
                if disconnected(str(exc)):
                    raise CaptureDisconnected(str(exc))
                raise ValueError('当前帧无法解析名称 %s：%s；若它是宏，需要编译时保留宏调试信息（GCC -g3）' % (name, exc))

        def read(node, depth=0):
            operations[0] -= 1
            if operations[0] < 0 or depth > 12:
                raise ValueError('达到表达式读取预算')
            if isinstance(node, ast.Name):
                return name_value(node.id)
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
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert)):
                number = integer(read(node.operand, depth+1))
                return ~number if isinstance(node.op, ast.Invert) else -number if isinstance(node.op, ast.USub) else number
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.BitOr, ast.BitAnd, ast.BitXor, ast.LShift, ast.RShift)):
                a, b = integer(read(node.left, depth+1)), integer(read(node.right, depth+1))
                if max(abs(a), abs(b)) > 2**63:
                    raise ValueError('索引整数运算范围过大')
                if isinstance(node.op, (ast.LShift, ast.RShift)):
                    if not 0 <= b <= 63:
                        raise ValueError('位移数量超出支持范围 0～63')
                    return a << b if isinstance(node.op, ast.LShift) else a >> b
                if isinstance(node.op, ast.BitOr): return a | b
                if isinstance(node.op, ast.BitAnd): return a & b
                if isinstance(node.op, ast.BitXor): return a ^ b
                return a+b if isinstance(node.op, ast.Add) else a-b if isinstance(node.op, ast.Sub) else a*b
            raise ValueError('不执行函数调用、赋值、自增或不支持的表达式')
        def integer(value):
            if isinstance(value, int):
                return value
            if value.type.strip_typedefs().code not in (gdb.TYPE_CODE_INT, gdb.TYPE_CODE_BOOL, gdb.TYPE_CODE_ENUM, gdb.TYPE_CODE_CHAR):
                raise ValueError('索引不是整数')
            if any(value is v for v in constants):
                return int(value)
            info = check_initialization(value, budget)
            if info.get('observed_status', info['status']) in ('undefined', 'partially_undefined', 'unaddressable'):
                raise ValueError('索引未初始化或不可访问，不据此读取元素')
            if info.get('status') != 'defined' or info.get('complete') is False:
                uncertain_operands.append(True)
            return int(value)
        try:
            if '++' in expression or '--' in expression:
                raise ValueError('不执行自增或自减')
            expanded = expression
            if macro_scope and '\n' not in expression and '\r' not in expression:
                try:
                    output = gdb.execute('macro expand ' + expression, to_string=True)
                    if disconnected(output):
                        raise CaptureDisconnected(output)
                    match = re.search(r'^expands to:\s*(.*)', output, re.S | re.M)
                    if match and len(match.group(1)) <= 4096:
                        expanded = match.group(1).strip()
                        if expanded != expression:
                            row['macro_expansion'] = expanded
                except Exception as exc:
                    if disconnected(str(exc)):
                        raise CaptureDisconnected(str(exc))
                    row['macro_note'] = 'GDB 未提供宏展开信息'
            if '++' in expanded or '--' in expanded:
                raise ValueError('宏展开含自增或自减，不执行')
            value = read(parse(expanded))
            if isinstance(value, int):
                row.update(status='available', type='整数表达式', value=str(value), initialization={'status':'unknown' if uncertain_operands else 'defined', 'reason':'常量或整数表达式结果；运算数检查不完整时不能确认初始化状态'})
                results.append(row)
                continue
            row['type'] = str(value.type)
            if value.is_optimized_out:
                row.update(status='optimized_out', value='已被编译器优化，无法读取')
            else:
                kind = value.type.strip_typedefs().code
                if kind in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION, gdb.TYPE_CODE_ARRAY):
                    row.update(status='unavailable', value='本行是整个对象，未展开未明确指定的成员')
                else:
                    row.update(status='available', value=value.format_string(raw=True)[:4096])
                    row['initialization'] = ({'status':'defined', 'reason':'枚举或符号常量，无需检查初始化'} if any(value is v for v in constants) else check_initialization(value, budget))
        except Exception as exc:
            if disconnected(str(exc)):
                raise CaptureDisconnected(str(exc))
            row['value'] = ('表达式含暂不支持的 C/C++ 语法，未执行；' if isinstance(exc, SyntaxError) else '') + str(exc)
        results.append(row)
    return {'version': 1, 'items': results, 'truncated': len(expressions) > 32,
            'note': '逐项读取当前帧的源码表达式；不执行目标函数。初始化检查不等于业务取值检查，也不能单独证明本次错误原因。'}
'''
