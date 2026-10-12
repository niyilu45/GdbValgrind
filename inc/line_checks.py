"""Bounded, read-only checks of expressions on the stopped source line."""

LINE_CHECK_SCRIPT = r'''
import ast
from copy import deepcopy

def allocation_location(address, budget):
    cache = budget.setdefault('allocation_locations', {})
    if address in cache:
        return cache[address]
    result = {'address':hex(address), 'status':'unknown'}
    if len(cache) >= 32:
        result['reason'] = '本次现场分配信息查询达到 32 个地址上限'
        return result
    try:
        output = capture_execute('monitor v.info location ' + hex(address))
        result['evidence'] = output[:2048]
        # A nearby block or another address's description is not ownership.
        pattern = r'Address\s+(0x[0-9a-f]+)\s+is\s+([\d,]+) bytes inside a block of size ([\d,]+)\s+(alloc\S*|free\S*)'
        matches = []
        for match in re.finditer(pattern, output, re.I):
            actual, offset, size, state = match.groups()
            offset, size = int(offset.replace(',', '')), int(size.replace(',', ''))
            if int(actual,16) == address and 0 <= offset < size and offset <= address:
                matches.append((address-offset,size,'freed' if state.lower().startswith('free') else 'allocated'))
        if matches and len(set(matches)) == 1:
            start,size,state = matches[0]
            result.update(status=state,start=start,size=size,end=start+size,
                          start_address=hex(start),end_address=hex(start+size-1))
        else:
            result['reason'] = '未取得该地址所在分配块的明确描述（可能为栈/全局内存、附近块描述或命令不支持）'
    except Exception as exc:
        if disconnected(str(exc)):
            raise CaptureDisconnected(str(exc))
        result['reason'] = str(exc)
    cache[address] = result
    return result

def check_allocation_range(address, stride, offset, width, index, budget):
    base = allocation_location(address,budget)
    target_address = address+index*stride+offset
    target = allocation_location(target_address,budget)
    tail = allocation_location(target_address+width-1,budget)
    result = {'base':base,'target':target,'target_end':tail,'status':'unknown','ranges':[],
              'note':'只判断目标成员与指针当前所在分配块的关系；不证明业务下标有效，也无法识别同址重新分配之前的悬空指针。'}
    if base['status'] == 'freed':
        result.update(status='freed',ranges=[{'start':0,'end':index,'status':'freed'}])
    elif base['status'] == 'allocated':
        last = min(index,(base['end']-address-offset-width)//stride)
        if last >= 0:
            result['ranges'].append({'start':0,'end':last,'status':'same_block'})
        if last < index:
            result['ranges'].append({'start':max(0,last+1),'end':index,'status':'outside_block'})
        result['status'] = 'same_block' if last == index else 'outside_block'
        if result['status'] == 'same_block' and any(
                item['status'] != 'unknown' and (item['status'] != 'allocated' or
                (item['start'],item['size']) != (base['start'],base['size'])) for item in (target,tail)):
            result.update(status='unknown',ranges=[],reason='分配块描述相互矛盾，不推断归属')
        if result['status'] == 'outside_block' and target['status'] == 'allocated':
            result['reason'] = '目标地址虽然属于存活分配块，但已经越过指针原来所在块的边界'
    elif target['status'] == 'freed' or tail['status'] == 'freed':
        result['status'] = 'target_freed'
    if not result['ranges']:
        result['ranges'] = [{'start':0,'end':index,'status':'unknown'}]
    return result

def check_array_prefix(base, index, low, high, budget, member_path=(), dynamic=False):
    """Batch validity queries; compress adjacent elements with the same state."""
    result = {'requested': [0, index], 'bounds': None if dynamic else [low, high], 'ranges': [],
              'range_basis': 'index_probe' if dynamic else 'declared_array',
              'checked_elements': 0, 'coverage_complete': False,
              'evidence_version': 1, 'checked_bytes': 0, 'queried_bytes': 0, 'queries': [],
              'member_path': '.'.join(member_path),
              'attempted_elements': 0, 'failed_elements': 0, 'query_errors': [],
              'note': '检查数组元素自身的存储；通过不代表业务取值合理，也不证明指针指向对象已分配。'}
    if dynamic:
        result['note'] = '按索引推导的待检查范围，不是合法数组范围；分配长度未知。即使地址可访问，也不证明属于同一分配对象或业务取值正确。'
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
            selected_type, member_offset = element, 0
            for name in member_path:
                selected_type = selected_type.strip_typedefs()
                if selected_type.code != gdb.TYPE_CODE_STRUCT:
                    raise ValueError('成员路径跨越指针或不支持的布局，不展开其他成员')
                fields = [f for f in selected_type.fields() if getattr(f, 'name', None) == name]
                if len(fields) != 1:
                    raise ValueError('无法唯一确定成员布局：' + name)
                field = fields[0]
                bitpos = getattr(field, 'bitpos', None)
                if bitpos is None or bitpos < 0 or bitpos % 8 or getattr(field, 'bitsize', 0):
                    raise ValueError('静态成员、位域或未知成员偏移，未检查该成员范围')
                member_offset += bitpos // 8
                selected_type = field.type
            width = int(selected_type.sizeof)
            if stride <= 0 or width <= 0 or width > 65536 or member_offset+width > stride:
                raise ValueError('目标成员大小或偏移不可用，或单次目标超过 65536 字节')
            result['note'] += (' 仅检查每个元素的 ' + '.'.join(member_path) + ' 成员，其他成员不参与判定。') if member_path else ''
            offsets, layout_complete = initialization_layout(selected_type, width)
            used = sorted(offsets)
            if dynamic and base.type.strip_typedefs().code == gdb.TYPE_CODE_PTR:
                pointer_info = check_initialization(base, budget)
                result['pointer_initialization'] = pointer_info
                if pointer_info.get('status') != 'defined' or pointer_info.get('complete') is False:
                    raise ValueError('指针自身未确认完整初始化，不使用其地址探测成员范围')
                address = int(base)
                if address == 0:
                    raise ValueError('空指针，不探测其派生成员地址')
                address_max = (1 << (int(base.type.sizeof)*8)) - 1
                if address < 0 or address + (end-low)*stride + member_offset + width - 1 > address_max:
                    raise ValueError('待检查地址超出目标指针地址范围，不进行溢出地址探测')
            else:
                if base.address is None:
                    raise ValueError('数组没有可取地址的存储，无法查询内存有效性')
                address = int(base.address)
                if dynamic:
                    address_max = (1 << (int(base.type.pointer().sizeof)*8)) - 1
                    if address <= 0 or address+(end-low)*stride+member_offset+width-1 > address_max:
                        raise ValueError('尾部数组探测地址不可用或溢出，不继续查询')
            cache = budget.setdefault('array_prefix_cache', {})
            cache_key = (address, str(element), stride, low, high, index, tuple(member_path), member_offset, width, dynamic)
            if cache_key in cache:
                cached = deepcopy(cache[cache_key])
                cached['reused'] = True
                return cached
            if dynamic:
                result['allocation'] = check_allocation_range(address,stride,member_offset,width,index,budget)
            result.update(element_bytes=width, element_stride=stride, target_bytes=(end-start+1)*width,
                          start_address='0x%x' % (address+(start-low)*stride+member_offset))
            # Separate budget: scanning a prefix must not consume the current
            # element's normal checks. Shared across all frames in one stop.
            remaining = budget.setdefault('array_prefix', {'queries': 256, 'bytes': 1048576, 'ranges': 512})
            result['budget_before'] = dict(remaining)
            while cursor <= end:
                if remaining['queries'] <= 0 or remaining['bytes'] < width or remaining['ranges'] <= 0:
                    raise ValueError('数组范围检查预算不足：每项目标 %d 字节，剩余 %d 字节、%d 次查询、%d 段结果；尚有 %d 个元素未检查' % (width, remaining['bytes'], remaining['queries'], remaining['ranges'], end-cursor+1))
                count = min(end - cursor + 1, max(1, 4096 // width) if width == stride else 1, remaining['bytes'] // width)
                length = count * width
                query_address = address + (cursor-low)*stride + member_offset
                bits = []
                query_failed = False
                # Large struct elements must not produce an oversized monitor
                # request. Gather all chunks before classifying the element.
                for byte_offset in range(0, length, 4096):
                    chunk_size = min(4096, length-byte_offset)
                    if remaining['queries'] <= 0:
                        raise ValueError('结构体元素分块检查时查询预算已耗尽，该元素及后续元素未完成检查')
                    remaining['queries'] -= 1
                    remaining['bytes'] -= chunk_size
                    chunk_address = query_address+byte_offset
                    response = ''
                    try:
                        response = capture_execute('monitor get_vbits 0x%x %d' % (chunk_address, chunk_size))
                        bits.extend(parse_vbits(response, chunk_size))
                    except Exception as exc:
                        if disconnected(str(exc)):
                            raise CaptureDisconnected(str(exc))
                        query_failed = True
                        if len(result['query_errors']) < 16:
                            result['query_errors'].append({'start':cursor, 'end':cursor+count-1,
                                'address':'0x%x' % chunk_address, 'bytes':chunk_size,
                                'reason':str(exc), 'response':response[:512]})
                        break
                    result['queries'].append({'address': '0x%x' % chunk_address, 'bytes': chunk_size})
                    result['queried_bytes'] += chunk_size
                result['attempted_elements'] += count
                if query_failed:
                    before = len(result['ranges'])
                    append(cursor, cursor+count-1, 'unknown')
                    remaining['ranges'] -= len(result['ranges'])-before
                    result['failed_elements'] += count
                    cursor += count
                    continue
                for offset in range(count):
                    if remaining['ranges'] <= 0:
                        raise ValueError('结果区间过多，剩余范围未检查')
                    storage = bits[offset*width:(offset+1)*width]
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
                    result['checked_bytes'] += width
                    result['end_address'] = '0x%x' % (address+(cursor-low)*stride+member_offset+width-1)
                    cursor += 1
    except Exception as exc:
        if disconnected(str(exc)):
            raise CaptureDisconnected(str(exc))
        result['reason'] = str(exc)
    append(cursor, end, 'unchecked')
    append(max(0, high + 1), index, 'out_of_bounds')
    result['coverage_complete'] = cursor > end and result['failed_elements'] == 0
    if result['failed_elements']:
        result['reason'] = (result.get('reason', '') + '；' if result.get('reason') else '') + '%d 个元素的查询失败，已继续尝试后续范围；失败项标为无法确定，不能判为正常' % result['failed_elements']
    if result['coverage_complete'] and start <= end and len(cache) < 128:
        cache[cache_key] = deepcopy(result)
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
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript):
                    current, path = node, []
                    while isinstance(parents.get(current), ast.Attribute) and parents[current].value is current:
                        current = parents[current]
                        path.append(current.attr)
                    node._aiv_member_path = tuple(path)
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
                access['base_type'] = str(kind)
                path = getattr(node, '_aiv_member_path', ())
                if kind.code == gdb.TYPE_CODE_PTR:
                    access['prefix_check'] = check_array_prefix(base, index, 0, index, budget, path, dynamic=True)
                    if access['prefix_check'].get('allocation', {}).get('status') in ('outside_block','freed','target_freed'):
                        raise ValueError('目标成员已越过原分配块或位于已释放内存；仅保留检查结果，不读取其值')
                    current = next((r['status'] for r in access['prefix_check']['ranges'] if r['start'] <= index <= r['end']), 'unchecked')
                    if current not in ('defined', 'undefined'):
                        raise ValueError('动态指针分配长度未知，当前目标成员未确认可访问；仅保留区间探测结果，不读取其值')
                    return base[index]
                if kind.code != gdb.TYPE_CODE_ARRAY:
                    raise ValueError('动态指针长度未知，未读取该索引元素')
                low, high = map(int, kind.range())
                access['bounds'] = [low, high]
                if low == 0 and high in (-1,0) and index > high:
                    # Tail storage may be declared as [0]/[1]. Probe the
                    # requested addresses, but never reinterpret it as legal
                    # declared indexing or read a value outside that bound.
                    access['prefix_check'] = check_array_prefix(base,index,0,index,budget,path,dynamic=True)
                    access['prefix_check']['range_basis'] = 'tail_array_probe'
                    access['prefix_check']['declared_bounds'] = [low,high]
                    access['prefix_check']['note'] = '声明仅有 0/1 项，按实际索引探测目标成员；可能为尾部扩展存储，也可能是真实越界。探测不改变声明边界，不能据此认定下标合法。'
                else:
                    access['prefix_check'] = check_array_prefix(base, index, low, high, budget, path) if path else check_array_prefix(base, index, low, high, budget)
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
