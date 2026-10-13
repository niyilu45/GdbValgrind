"""Shared parsing of saved diagnostics and live GDB captures."""
import re

MEMORY_DIAGNOSTICS_SCRIPT = r'''
def memory_facts(message, location=''):
    facts = {}
    access = re.search(r'Invalid (read|write) of size ([\d,]+)', message, re.I)
    if access:
        facts['access'] = access.group(1).lower()
        facts['access_bytes'] = int(access.group(2).replace(',', ''))
    pattern = r'\b(?:Address|Location)\s+(0x[0-9a-f]+)\b'
    address = re.search(pattern, message, re.I)
    if address:
        facts['address'] = address.group(1)
        facts['address_source'] = 'Valgrind error diagnostic'
    # An auxiliary address must not supply a block size for the fault address.
    address_line = message[address.start():].split('\n', 1)[0] if address else ''
    facts['address_description'] = address_line.strip()
    block_pattern = r'([\d,]+) bytes? (after|before|inside) a block of size ([\d,]+)\s+(alloc\S*|free\S*)'
    block = re.search(block_pattern, address_line, re.I)
    # Only enrich an address already identified in this error. A lookup must
    # never invent a fault address or supply a different block's boundaries.
    if address and not block:
        for candidate in re.finditer(pattern, message+'\n'+location, re.I):
            if int(candidate.group(1),16) != int(address.group(1),16):
                continue
            line = (message+'\n'+location)[candidate.start():].split('\n',1)[0]
            found = re.search(block_pattern, line, re.I)
            if found:
                block, address_line = found, line
                break
    if block:
        distance, relation, size, state = block.groups()
        facts.update(distance_bytes=int(distance.replace(',', '')), relation=relation.lower(),
                     block_bytes=int(size.replace(',', '')), block_state=state.lower())
        facts.update(range_evidence_version=2, range_evidence=address_line.strip())
        if state.lower().startswith('free'):
            facts['explanation'] = '访问地址对应已释放的内存块；请检查释放栈和访问栈。'
        elif relation.lower() == 'after':
            facts['explanation'] = '访问起始地址位于分配块末尾之后 %s 字节（0 表示紧邻块末尾），属于块尾越界。' % distance
        elif relation.lower() == 'before':
            facts['explanation'] = '访问起始地址位于分配块起始地址之前 %s 字节，属于块首越界。' % distance
        elif 'access_bytes' in facts and facts['distance_bytes'] + facts['access_bytes'] > facts['block_bytes']:
            facts['explanation'] = '访问从块内偏移 %s 字节开始，但访问长度超出了分配块末尾。' % distance
        if 'access_bytes' in facts:
            length, width, distance = facts['block_bytes'], facts['access_bytes'], facts['distance_bytes']
            offset = length + distance if facts['relation']=='after' else -distance if facts['relation']=='before' else distance
            facts['actual_offset_bytes'] = offset
            facts['actual_last_offset_bytes'] = offset + width - 1
            freed = facts['block_state'].startswith('free')
            facts['valid_start_offset_bytes'] = [0, length-width] if not freed and width>0 and width<=length else None
            valid = facts['valid_start_offset_bytes']
            allowed = ('0 到 %d 字节' % valid[1]) if valid else '无（内存已释放或访问宽度超过块大小）'
            facts['range_explanation'] = '诊断提及的内存块大小 %d 字节；本次访问 %d 字节。仅按该块边界计算，可容纳访问的起始偏移为 %s；实际起始偏移 %d 字节，覆盖偏移 %d 到 %d 字节。该块可能只是报错地址附近的分配块，尚未确认对应哪个变量；此范围不是数组下标，也不证明访问合法。' % (length,width,allowed,offset,offset,offset+width-1)
            if facts.get('address'):
                start = int(facts['address'], 16) - offset
                facts['block_start_address'] = hex(start)
                if valid:
                    facts['valid_start_addresses'] = [hex(start), hex(start+valid[1])]
                    facts['range_explanation'] += ' 该块内可容纳访问的起始地址：%s～%s；实际访问地址：%s。' % (*facts['valid_start_addresses'], facts['address'])
    if access and 'range_explanation' not in facts:
        descriptions = [address_line]
        if address:
            for match in re.finditer(pattern, location, re.I):
                if int(match.group(1),16) == int(address.group(1),16):
                    descriptions.append(location[match.start():].split('\n',1)[0])
        description = ' '.join(descriptions)
        if address and int(address.group(1),16) == 0:
            category, reason = 'null', '本次访问从空地址 0x0 开始；该地址不是可用对象，需检查空指针及其来源。'
        elif re.search(r'not stack.d, malloc.d or .*free.d|not mapped|unallocated', description, re.I):
            category, reason = 'unallocated', 'Valgrind 未将访问地址识别为栈、已分配或已释放的内存；没有可引用的对象边界。需检查无效指针或已被破坏的地址，不能直接认定为数组越界。'
        elif re.search(r'global var|data symbol', description, re.I):
            category, reason = 'global', '访问地址位于全局或静态存储；应依据对应变量的地址和类型大小核查，不使用堆分配块边界。'
        elif re.search(r'local var|on thread .*stack|below .*stack', description, re.I):
            category, reason = 'stack', '访问涉及栈内存；应核查局部对象范围及生命周期，不使用堆分配块边界。'
        elif re.search(r'bytes? inside (?!a block)', description, re.I):
            category, reason = 'object', 'Valgrind 提供了对象位置，但没有提供对象总大小；需要结合调试类型核查范围。'
        else:
            category, reason = 'unknown', '尚不能确定访问地址属于哪个对象，无法给出可信的合法范围。'
        facts.update(storage_category=category, explanation=reason)
        facts['range_explanation'] = '实际访问地址：%s；访问宽度：%s 字节。%s' % (facts.get('address', '未提供'), facts['access_bytes'], reason)
        if not address:
            facts['missing_reason'] = '本次原始错误中没有可识别的 Address/Location 地址行；未使用调用栈指令地址或历史错误地址代替。'
        elif category == 'unknown':
            facts['missing_reason'] = '已取得地址，但当前诊断和地址查询未能确认所属对象；不会从附近内存块推定合法范围。'
    return facts

def fault_boundary_findings(snapshot, address, width):
    """Relate the actual access to its base allocation, not a nearby block."""
    findings = []
    frames = snapshot.get('frames', [])
    for item in (frames[0].get('line_checks', {}).get('items', []) if frames else []):
        candidates = []
        for access in item.get('index_accesses', []):
            prefix = access.get('prefix_check', {})
            expression = access.get('expression', '') + ('.'+prefix['member_path'] if prefix.get('member_path') else '')
            candidates.append((expression, prefix.get('target_storage', {}),
                               prefix.get('allocation', {}).get('base', {}), prefix.get('address_calculation'), access.get('index_expression')))
        for pointer in item.get('pointer_targets', []):
            candidates.append((pointer.get('expression',''), pointer.get('target_storage',{}), pointer.get('allocation',{}), None, None))
        for expression, target, block, calculation, index_expression in candidates:
            if re.sub(r'\s+', '', expression) != re.sub(r'\s+', '', item.get('name', '')):
                continue
            try:
                if int(target['address'],16) != address or block.get('status') not in ('allocated','freed'):
                    continue
                start, size = int(block['start']), int(block['size'])
                base = int(block['address'],16)
                if size <= 0 or not start <= base < start+size or not block.get('evidence'):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            end = start+size
            before = max(0, min(address+width,start)-address)
            after = max(0, address+width-max(address,end))
            finding = {'expression':expression, 'base_address':hex(base), 'block_start':hex(start),
                       'block_end':hex(end-1), 'block_bytes':size, 'access_address':hex(address),
                       'access_bytes':width, 'actual_offset':address-start,
                       'valid_start_offsets':[0,size-width] if size >= width and block['status']=='allocated' else None,
                       'outside_bytes':before+after, 'evidence':block['evidence']}
            if block['status'] == 'freed':
                finding.update(kind='use_after_free', explanation='该表达式的基址指针指向已释放内存块；本次访问沿用该指针，需检查释放后使用。')
            elif before or after:
                finding.update(kind='out_of_allocation', explanation='该表达式的访问越过基址指针所在分配块，%d 个访问字节位于块外。' % (before+after))
            else:
                finding.update(kind='inside_allocation', explanation='本次访问位于基址指针所在分配块内；堆块边界不能解释该错误，还需检查子对象范围、可访问标记或对象生命周期。')
            if calculation and block['status'] == 'allocated':
                try:
                    origin = int(calculation['base_address'],16)
                    stride, offset, index, low = (int(calculation[k]) for k in ('stride','member_offset','index','lower_bound'))
                    if stride > 0 and origin == base and origin+(index-low)*stride+offset == address:
                        lower = max(0, low-((origin+offset-start)//stride))
                        upper = low+(end-origin-offset-width)//stride
                        finding.update(actual_index=index, index_expression=index_expression or '本次下标', member_offset=offset, stride=stride,
                                       block_index_range=[lower,upper] if upper >= lower else None)
                except (KeyError, TypeError, ValueError):
                    pass
            if finding not in findings:
                findings.append(finding)
    return findings

def analyze_fault_variables(snapshot):
    """Address correspondence only; never infer causality from a green V-bit check."""
    result = {'version': 1, 'status': 'unknown', 'candidates': [],
              'note': '未将非法访问对应到源码表达式；检查通过的变量不能排除本次非法访问。'}
    if snapshot.get('association', {}).get('status') != 'verified':
        return result
    memory = snapshot.get('memory', {})
    try:
        address, width = int(memory['address'], 16), int(memory['access_bytes'])
        if memory.get('access') not in ('read', 'write') or width <= 0:
            return result
    except (KeyError, ValueError, TypeError):
        return result
    result.update(address=hex(address), access_bytes=width,
                  last_address=hex(address+width-1), cause=memory.get('explanation', '现有诊断不足以确定越界或释放原因。'))
    result['boundary_findings'] = fault_boundary_findings(snapshot, address, width)
    if len(result['boundary_findings']) == 1:
        result['cause'] = result['boundary_findings'][0]['explanation']
    context = snapshot.get('instruction_context', {})
    result['instruction_address_verified'] = bool(context.get('pc') == snapshot['association'].get('current_pc') and
        any(op.get('matches_fault_address') and op.get('registers_precise') for op in context.get('operands', [])))
    # Only the faulting frame, never caller arguments or another grouped stop.
    frames = snapshot.get('frames', [])
    for item in (frames[0].get('line_checks', {}).get('items', []) if frames else []):
        storage = item.get('storage') or item.get('initialization', {})
        if not storage.get('address'):
            for access in item.get('index_accesses', []):
                prefix = access.get('prefix_check', {})
                expression = access.get('expression', '')
                if prefix.get('member_path'):
                    expression += '.' + prefix['member_path']
                if re.sub(r'\s+', '', expression) == re.sub(r'\s+', '', item.get('name', '')):
                    storage = prefix.get('target_storage', {})
        try:
            start, size = int(storage['address'], 16), int(storage['size_bytes'])
        except (KeyError, ValueError, TypeError):
            continue
        if size <= 0 or not start <= address < start+size:
            continue
        result['candidates'].append({'name': item['name'], 'address': hex(start),
            'size_bytes': size, 'offset_bytes': address-start,
            'exceeds_expression': address+width > start+size})
    count = len(result['candidates'])
    result['status'] = 'single_candidate' if count == 1 else 'multiple_candidates' if count else 'unknown'
    if count:
        result['note'] = ('找到一个地址对应表达式，仍需结合指令确认它是否导致错误。' if count == 1 else
                          '多个表达式覆盖同一访问地址，可能存在别名，不能唯一确定错误变量。')
        if 'block_bytes' not in memory:
            result['boundary_note'] = '已从 GDB 类型取得下列候选表达式的存储大小，可对照访问长度；这不是堆分配长度，也不能单独证明对象仍存活。'
    check = context.get('access_check', {})
    if check.get('address') == memory.get('address') and not check.get('error') and check.get('unaddressable_offsets'):
        result['boundary_note'] = result.get('boundary_note', '') + ' 本次访问范围的直接检查发现 %d 个不可访问字节。' % len(check['unaddressable_offsets'])
    return result

'''

exec(compile(MEMORY_DIAGNOSTICS_SCRIPT, __file__, 'exec'))
