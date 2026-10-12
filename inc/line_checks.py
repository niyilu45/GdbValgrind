"""Bounded, read-only checks of expressions on the stopped source line."""

LINE_CHECK_SCRIPT = r'''
import ast

def check_array_prefix(base, index, low, high, budget):
    """Batch validity queries; compress adjacent elements with the same state."""
    result = {'requested': [0, index], 'bounds': [low, high], 'ranges': [],
              'checked_elements': 0, 'coverage_complete': False,
              'evidence_version': 1, 'checked_bytes': 0, 'queried_bytes': 0, 'queries': [],
              'note': '检查数组元素自身的存储；通过不代表业务取值合理，也不证明指针指向对象已分配。'}
    def append(first, last, state):
        if first > last:
            return
        ranges = result['ranges']
        if ranges and ranges[-1]['status'] == state and ranges[-1]['end'] + 1 == first:
            ranges[-1]['end'] = last
        else:
            ranges.append({'start': first, 'end': last, 'status': state})
    if index < 0:
        result['reason'] = '当前下标为负数，不存在从 0 到当前下标的正向检查区间'
        return result
    start, end = max(0, low), min(index, high)
    append(0, min(index, low - 1), 'out_of_bounds')
    cursor = start
    try:
        if start <= end:
            element = base.type.strip_typedefs().target()
            stride = int(element.sizeof)
            if stride <= 0 or stride > 65536:
                raise ValueError('单个元素大小未知或超过 65536 字节，未检查该范围')
            offsets, layout_complete = initialization_layout(element, stride)
            used = sorted(offsets)
            if base.address is None:
                raise ValueError('数组没有可取地址的存储，无法查询内存有效性')
            address = int(base.address)
            result.update(element_bytes=stride, target_bytes=(end-start+1)*stride,
                          start_address='0x%x' % (address+(start-low)*stride))
            # Separate budget: scanning a prefix must not consume the current
            # element's normal checks. Shared across all frames in one stop.
            remaining = budget.setdefault('array_prefix', {'queries': 256, 'bytes': 1048576, 'ranges': 512})
            while cursor <= end:
                if remaining['queries'] <= 0 or remaining['bytes'] < stride or remaining['ranges'] <= 0:
                    raise ValueError('本次现场数组范围检查达到预算（1 MiB、256 次批量查询或 512 段结果），剩余范围未检查')
                count = min(end - cursor + 1, max(1, 4096 // stride), remaining['bytes'] // stride)
                length = count * stride
                remaining['queries'] -= 1
                remaining['bytes'] -= length
                query_address = address + (cursor-low)*stride
                bits = parse_vbits(capture_execute('monitor get_vbits 0x%x %d' % (query_address, length)), length)
                result['queries'].append({'address': '0x%x' % query_address, 'bytes': length})
                result['queried_bytes'] += length
                for offset in range(count):
                    if remaining['ranges'] <= 0:
                        raise ValueError('结果区间过多，剩余范围未检查')
                    storage = bits[offset*stride:(offset+1)*stride]
                    selected = [storage[n] for n in used]
                    if None in storage:
                        state = 'unaddressable'
                    elif any(value != 0 for value in selected):
                        state = 'undefined'
                    elif not used or not layout_complete:
                        state = 'unknown'
                    else:
                        state = 'defined'
                    before = len(result['ranges'])
                    append(cursor, cursor, state)
                    remaining['ranges'] -= len(result['ranges']) - before
                    result['checked_elements'] += 1
                    result['checked_bytes'] += stride
                    result['end_address'] = '0x%x' % (address+(cursor-low+1)*stride-1)
                    cursor += 1
    except Exception as exc:
        if disconnected(str(exc)):
            raise CaptureDisconnected(str(exc))
        result['reason'] = str(exc)
    append(cursor, end, 'unchecked')
    append(max(0, high + 1), index, 'out_of_bounds')
    result['coverage_complete'] = cursor > end
    return result

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
    text = (source.get('control_header') or source).get('identifiers', '')[:4096]
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
            text = re.sub(r'\s+', ' ', text).strip()
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
            # Keep arrow width so AST columns still point into the C++ text.
            tree = ast.parse(text.replace('->', '. '), mode='eval').body
            for node in ast.walk(tree):
                label = ast.get_source_segment(text, node)
                if label:
                    node._aiv_label = re.sub(r'\b[A-Za-z_]\w*\b', lambda m: aliases.get(m.group(), m.group()), label)
            return tree

        def name_value(name):
            name = aliases.get(name, name)
            symbol, implicit, lookup_error = None, False, ''
            if hasattr(gdb, 'lookup_symbol'):
                try:
                    symbol, implicit = gdb.lookup_symbol(name, frame.block())
                except Exception as exc:
                    if disconnected(str(exc)):
                        raise CaptureDisconnected(str(exc))
                    lookup_error = str(exc)
            if implicit or symbol is not None:
                if implicit:
                    owner = frame.read_var('this')
                    if owner.type.strip_typedefs().code == gdb.TYPE_CODE_PTR:
                        owner = owner.dereference()
                    return owner[name]
                if symbol is not None:
                    if not any(getattr(symbol, flag, False) for flag in ('is_variable', 'is_argument', 'is_constant')):
                        raise ValueError('该名称不是变量或枚举常量：' + name)
                    try:
                        value = symbol.value(frame)
                    except Exception as exc:
                        if disconnected(str(exc)):
                            raise CaptureDisconnected(str(exc))
                        # Some GDB/debug-info combinations can read the symbol
                        # through Frame.read_var even when Symbol.value fails.
                        try:
                            value = frame.read_var(symbol)
                        except Exception as fallback_exc:
                            if disconnected(str(fallback_exc)):
                                raise CaptureDisconnected(str(fallback_exc))
                            lookup_error = str(exc) + '；' + str(fallback_exc)
                            value = None
                    if value is not None:
                        if getattr(symbol, 'is_constant', False):
                            constants.append(value)
                        return value
            try:
                return frame.read_var(name)
            except Exception as exc:
                if disconnected(str(exc)):
                    raise CaptureDisconnected(str(exc))
                raise ValueError('当前帧无法解析名称 %s：%s%s；若它是宏，需要编译时保留宏调试信息（GCC -g3）；局部变量也可能已优化或不在当前停止位置的作用域内' % (name, exc, ('；符号查找：'+lookup_error) if lookup_error else ''))

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
                if value.type.strip_typedefs().code in (getattr(gdb, 'TYPE_CODE_REF', -1), getattr(gdb, 'TYPE_CODE_RVALUE_REF', -2)):
                    value = value.referenced_value()
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
                if base.type.strip_typedefs().code in (getattr(gdb, 'TYPE_CODE_REF', -1), getattr(gdb, 'TYPE_CODE_RVALUE_REF', -2)):
                    base = base.referenced_value()
                if source_is_map(base):
                    key = read(node.slice, depth+1)
                    if not isinstance(key, (int, str)):
                        key_info = check_initialization(key, budget)
                        if key_info.get('observed_status', key_info['status']) in ('undefined', 'partially_undefined', 'unaddressable'):
                            raise ValueError('字典键未初始化或不可访问，不据此读取元素')
                    return source_map_lookup(base, key)
                index = integer(read(node.slice, depth+1))
                access = {'expression':getattr(node, '_aiv_label', expression),
                          'base_expression':getattr(node.value, '_aiv_label', ''),
                          'index_expression':getattr(node.slice, '_aiv_label', ''),
                          'actual_index':index, 'bounds':None}
                row.setdefault('index_accesses', []).append(access)
                kind = base.type.strip_typedefs()
                if kind.code != gdb.TYPE_CODE_ARRAY:
                    raise ValueError('动态指针长度未知，未读取该索引元素')
                low, high = map(int, kind.range())
                access['bounds'] = [low, high]
                access['prefix_check'] = check_array_prefix(base, index, low, high, budget)
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
            if value.type.strip_typedefs().code in (getattr(gdb, 'TYPE_CODE_REF', -1), getattr(gdb, 'TYPE_CODE_RVALUE_REF', -2)):
                value = value.referenced_value()
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
                if source_is_map(value):
                    row.update(status='available', value=source_map_summary(value), initialization={'status':'unknown', 'reason':'仅只读展示容器内容，未验证容器内部结构或整次访问是否合法'})
                elif kind in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION, gdb.TYPE_CODE_ARRAY):
                    row.update(status='unavailable', value='本行是整个对象，未展开未明确指定的成员')
                else:
                    row.update(status='available', value=value.format_string(raw=True)[:4096])
                    row['initialization'] = ({'status':'defined', 'reason':'枚举或符号常量，无需检查初始化'} if any(value is v for v in constants) else check_initialization(value, budget))
        except Exception as exc:
            if disconnected(str(exc)):
                raise CaptureDisconnected(str(exc))
            if row['status'] == 'available':
                row['initialization'] = {'status': 'unknown', 'reason': '值已读取，但后续检查失败：' + str(exc)}
            else:
                row['value'] = ('表达式含暂不支持的 C/C++ 语法，未执行；' if isinstance(exc, SyntaxError) else '') + str(exc)
        results.append(row)
    coverage = {'recognized':len(expressions), 'attempted':len(results),
                'read':sum(r['status']=='available' for r in results),
                'unread':sum(r['status']!='available' for r in results),
                'initialization_unknown':sum(r['initialization'].get('status') in ('unknown','partial_check') for r in results)}
    return {'version': 1, 'items': results, 'coverage':coverage, 'truncated': len(expressions) > 32,
            'note': '逐项读取当前帧的源码表达式；不执行目标函数。初始化检查不等于业务取值检查，也不能单独证明本次错误原因。'}
'''
from .map_checks import MAP_CHECK_SCRIPT
LINE_CHECK_SCRIPT = MAP_CHECK_SCRIPT + LINE_CHECK_SCRIPT
