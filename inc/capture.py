# -*- coding: utf-8 -*-
"""Embedded collector executed inside GDB, not the host Python interpreter."""

GDB_CAPTURE_SCRIPT = r'''
import gdb
import json
import re
import html
from pathlib import Path
from datetime import datetime, timezone

def capture_execute(command):
    try:
        return gdb.execute(command, to_string=True)
    except Exception as exc:
        return '[unavailable: ' + str(exc) + ']'

def memory_facts(message):
    facts = {}
    access = re.search(r'Invalid (read|write) of size ([\d,]+)', message, re.I)
    if access:
        facts['access'] = access.group(1).lower()
        facts['access_bytes'] = int(access.group(2).replace(',', ''))
    address = re.search(r'Address\s+(0x[0-9a-f]+)', message, re.I)
    if address:
        facts['address'] = address.group(1)
    block = re.search(r'([\d,]+) bytes (after|before|inside) a block of size ([\d,]+)\s+(alloc\S*|free\S*)', message, re.I)
    if block:
        distance, relation, size, state = block.groups()
        facts.update(distance_bytes=int(distance.replace(',', '')), relation=relation.lower(),
                     block_bytes=int(size.replace(',', '')), block_state=state.lower())
        if state.lower().startswith('free'):
            facts['explanation'] = '访问地址对应已释放的内存块；请检查释放栈和访问栈。'
        elif relation.lower() == 'after':
            facts['explanation'] = '访问起始地址位于分配块末尾之后 %s 字节（0 表示紧邻块末尾），属于块尾越界。' % distance
        elif relation.lower() == 'before':
            facts['explanation'] = '访问起始地址位于分配块起始地址之前 %s 字节，属于块首越界。' % distance
        elif 'access_bytes' in facts and facts['distance_bytes'] + facts['access_bytes'] > facts['block_bytes']:
            facts['explanation'] = '访问从块内偏移 %s 字节开始，但访问长度超出了分配块末尾。' % distance
    return facts

def capture_identity(message):
    # Match diagnostic paths, never variable values or process-specific addresses.
    result = re.sub(r'==\d+==\s*', '', message)
    result = re.sub(r'0x[0-9a-fA-F]+', '<address>', result)
    result = re.sub(r'Thread\s+\d+', 'Thread <id>', result)
    result = re.sub(r'loss record \d+ of \d+', 'loss record <n>', result)
    return result.strip()

INIT_LABELS = {'defined': '已初始化', 'undefined': '未初始化',
               'partially_undefined': '部分未初始化', 'unaddressable': '内存不可访问',
               'unknown': '无法检查', 'partial_check': '仅完成部分检查'}

def parse_vbits(output, size):
    # Only accept lines consisting entirely of paired V-bit bytes. Do not parse
    # addresses or diagnostic prose as data, nor infer success from empty output.
    tokens = []
    for line in output.splitlines():
        line = line.strip()
        if re.fullmatch(r'(?:(?:[0-9a-fA-F]{2}|__)\s*)+', line):
            tokens.extend(re.findall(r'[0-9a-fA-F]{2}|__', line))
    if len(tokens) != size:
        raise ValueError('Valgrind 返回的初始化状态格式或长度不匹配')
    return [None if token == '__' else int(token, 16) for token in tokens]

def initialization_layout(value_type, limit, depth=0, members=None, prefix='', base=0):
    """Return member byte offsets only; never treat struct padding as a member."""
    t = value_type.strip_typedefs()
    size = int(t.sizeof)
    if depth > 5 or size <= 0:
        return set(), False
    scalars = [getattr(gdb, name, None) for name in ('TYPE_CODE_INT', 'TYPE_CODE_BOOL', 'TYPE_CODE_ENUM', 'TYPE_CODE_PTR', 'TYPE_CODE_CHAR')]
    if t.code in scalars:
        if members is not None:
            members.append({'name': prefix or '$self', 'byte_offsets': list(range(base, base + min(size, limit)))})
        return set(range(min(size, limit))), size <= limit
    if t.code == getattr(gdb, 'TYPE_CODE_ARRAY', -1):
        item_type = t.target()
        stride = int(item_type.sizeof)
        lo, hi = t.range()
        if stride <= 0 or hi < lo:
            return set(), False
        used, complete = set(), size <= limit
        for i in range(min(hi - lo + 1, (limit + stride - 1) // stride)):
            offsets, ok = initialization_layout(item_type, min(stride, limit - i * stride), depth + 1,
                                                members, prefix + '[%s]' % (i + lo), base + i * stride)
            used.update(i * stride + n for n in offsets)
            complete = complete and ok
        return used, complete
    if t.code == getattr(gdb, 'TYPE_CODE_STRUCT', -1):
        used, complete = set(), size <= limit
        for field in t.fields():
            # Static fields have no bitpos and are outside this object's storage.
            if not hasattr(field, 'bitpos'):
                continue
            if field.bitpos is None or field.bitpos < 0 or getattr(field, 'bitsize', 0) or field.bitpos % 8:
                complete = False
                continue
            offset = field.bitpos // 8
            if offset >= limit:
                complete = False
                continue
            field_name = getattr(field, 'name', None) or '<anonymous@%s>' % offset
            offsets, ok = initialization_layout(field.type, limit - offset, depth + 1,
                                                members, prefix + '.' + field_name if prefix else field_name, base + offset)
            used.update(offset + n for n in offsets if offset + n < limit)
            complete = complete and ok
        return used, complete
    # Union active members, references, bit fields and float padding need more
    # type/target knowledge. An honest unknown is better than a false diagnosis.
    return set(), False

def check_initialization(value, budget):
    result = {'status': 'unknown', 'scope': '变量自身存储；指针不自动检查其指向的内存'}
    try:
        if value.address is None:
            raise ValueError('变量没有可取地址的内存存储，可能位于寄存器')
        size = int(value.type.sizeof)
        length = min(size, 256)
        if length <= 0:
            raise ValueError('类型大小不可用')
        members = []
        used, complete = initialization_layout(value.type, length, members=members)
        if not used:
            raise ValueError('无法确定有效成员布局（如联合体、位域或不支持的类型）')
        if budget['queries'] <= 0 or budget['bytes'] < length:
            raise ValueError('本次现场初始化检查预算已用完')
        address = int(value.address)
        budget['queries'] -= 1
        budget['bytes'] -= length
        raw = capture_execute('monitor get_vbits 0x%x %d' % (address, length))
        result.update(address='0x%x' % address, size_bytes=size, checked_bytes=length, raw_vbits=raw,
                      complete=complete, member_byte_offsets=sorted(used))
        bits = parse_vbits(raw, length)
        selected = [bits[i] for i in sorted(used)]
        undefined = [i for i in sorted(used) if bits[i] is not None and bits[i] != 0]
        observed = ('unaddressable' if any(b is None for b in selected) else
                    'defined' if all(b == 0 for b in selected) else
                    'undefined' if all(b == 255 for b in selected) else 'partially_undefined')
        result.update(status=observed if complete else 'partial_check', observed_status=observed,
                      undefined_byte_offsets=undefined,
                      unaddressable_byte_offsets=[i for i in sorted(used) if bits[i] is None],
                      undefined_bits=sum(bin(bits[i]).count('1') for i in undefined),
                      ignored_padding_byte_offsets=[i for i in range(length) if i not in used] if complete else [])
        result['members'] = []
        result['member_states'] = []
        for member in members:
            member_bits = [bits[i] for i in member['byte_offsets'] if i < length]
            member_status = ('unaddressable' if None in member_bits else
                             'defined' if all(b == 0 for b in member_bits) else
                             'undefined' if all(b == 255 for b in member_bits) else 'partially_undefined')
            result['member_states'].append({'name': member['name'], 'status': member_status,
                                           'scope': '仅已检查字节；整个变量的检查范围见初始化状态'})
            member_undefined = [i for i in member['byte_offsets'] if i < length and bits[i] is not None and bits[i] != 0]
            if member_undefined:
                result['members'].append({'name': member['name'], 'undefined_byte_offsets': member_undefined})
        if not complete:
            result['reason'] = '仅检查前 256 字节或可解析成员；其余内容无法判定，不能推断整个变量已初始化'
    except Exception as exc:
        result['reason'] = str(exc)
    return result

def initialization_summary(info):
    label = INIT_LABELS.get(info['status'], info['status'])
    if info.get('status') == 'partial_check':
        label += '（已检查部分：' + INIT_LABELS.get(info.get('observed_status'), '未知') + '）'
    if info.get('undefined_byte_offsets'):
        label += '；未初始化字节偏移 ' + ', '.join(map(str, info['undefined_byte_offsets'][:32]))
    if info.get('reason'):
        label += '；' + info['reason']
    return label

def capture_source_line(filename, line):
    # Bounded source-only inspection; never evaluate expressions in the target.
    try:
        with open(filename, 'r', encoding='utf-8', errors='replace') as source:
            text = source.read(1024 * 1024)
        lines = text.splitlines()
        if not 0 < line <= len(lines) or (len(text) == 1024 * 1024 and line == len(lines)):
            return {}
        # Preserve line numbers while excluding comments and quoted literals.
        clean = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                       lambda m: '\n' * m.group().count('\n') or ' ', text, flags=re.S)
        return {'text': lines[line - 1][:4096], 'identifiers': clean.split('\n')[line - 1][:4096],
                'note': '源码文本关联，不代表该表达式已执行；宏、多行表达式和指针成员可能无法关联'}
    except (OSError, ValueError, IndexError):
        return {}

def line_variable_states(frame):
    source = frame.get('source_line', {}).get('identifiers', '')
    rows = []
    for variable in frame.get('variables', []):
        info = variable['initialization']
        members = info.get('member_states') or [{'name': '$self', 'status': info['status']}]
        for member in members:
            suffix = member['name']
            name = variable['name'] if suffix == '$self' else variable['name'] + ('' if suffix.startswith('[') else '.') + suffix
            pattern = re.escape(name).replace(r'\.', r'\s*\.\s*').replace(r'\[', r'\s*\[\s*').replace(r'\]', r'\s*\]')
            if source and re.search(r'(?<![\w.])' + pattern + r'(?!\w)', source):
                rows.append({'frame': frame['index'], 'name': name, 'status': member['status'],
                             'variable_status': info['status'], 'complete': info.get('complete', False),
                             'evidence': '仅源码同一行出现；未确认实际读取或因果关系'})
    return rows

def analyze_initialization_use(snapshot):
    message = snapshot['valgrind_error']
    is_use = bool(re.search(r'uninitiali[sz]ed|undefined value', message, re.I))
    # Ordinary undefined-branch reports contain no operand address. Do not guess
    # which local was used. A syscall-buffer diagnostic can identify a byte.
    direct = re.search(r'Syscall param[^\n]*points to uninitiali[sz]ed byte', message, re.I)
    address = re.search(r'Address\s+(0x[0-9a-f]+)', message, re.I) if direct else None
    bad_address = int(address.group(1), 16) if address else None
    findings = []
    for frame in snapshot['frames']:
        for variable in frame.get('variables', []):
            info = variable['initialization']
            if not info.get('undefined_byte_offsets'):
                continue
            start = int(info['address'], 16)
            for member in info.get('members', []):
                matched = bad_address is not None and bad_address - start in member['undefined_byte_offsets']
                name = variable['name'] if member['name'] == '$self' else variable['name'] + ('' if member['name'].startswith('[') else '.') + member['name']
                findings.append({'frame': frame['index'], 'name': name,
                                 'severity': 'error' if matched else 'warning',
                                 'usage': 'reported_address_match' if matched else 'not_observed',
                                 'evidence': ('本次系统调用错误的未初始化字节地址落在该成员内；属于地址关联证据。' if matched else
                                              '仅在现场扫描中发现未初始化，未发现该成员被本次错误使用的直接证据；不代表它在整个运行中从未被使用。'),
                                 'undefined_byte_offsets': member['undefined_byte_offsets']})
    line_variables = [row for frame in snapshot['frames'] for row in line_variable_states(frame)]
    return {'error_is_uninitialized_use': is_use,
            'line_variables': line_variables,
            'event_severity': 'error',
            'note': ('Valgrind 已报告使用未初始化值，但未必能确定具体变量；无直接使用证据的成员仅列警告。' if is_use else
                     '附加扫描发现的未初始化成员仅列警告，不将它们自动当作当前错误的原因。'),
            'limits': '每个变量最多 256 字节；每次现场最多 256 次查询、32768 字节。不自动解引用指针；寄存器变量、联合体和位域等可能无法完整检查。结构体填充字节不参与判定。',
            'findings': findings,
            'warning_count': sum(f['severity'] == 'warning' for f in findings),
            'error_member_count': sum(f['severity'] == 'error' for f in findings),
            'candidates': [{'frame': f['index'], 'name': v['name'], 'initialization': v['initialization']}
                           for f in snapshot['frames'] for v in f.get('variables', []) if v['initialization'].get('undefined_byte_offsets')]}

def capture_variables(frame, budget=None):
    if budget is None:
        budget = {'queries': 256, 'bytes': 32768}
    result, seen = [], set()
    try:
        block = frame.block()
        while block is not None and not block.is_global and not block.is_static:
            for symbol in block:
                if not (symbol.is_argument or symbol.is_variable):
                    continue
                name = symbol.name
                if not name or name in seen:
                    continue
                seen.add(name)
                row = {'name': name, 'role': 'argument' if symbol.is_argument else 'local'}
                try:
                    value = frame.read_var(symbol)
                    row['type'] = str(value.type)
                    if value.is_optimized_out:
                        row.update(status='optimized_out', value='变量已被编译器优化，无法读取')
                    elif getattr(value, 'is_unavailable', False):
                        row.update(status='unavailable', value='当前调试目标无法提供该值')
                    else:
                        rendered = value.format_string(raw=True)
                        row.update(status='available', value=rendered[:4096], truncated=len(rendered) > 4096)
                        row['initialization'] = check_initialization(value, budget)
                except Exception as exc:
                    row.update(status='unavailable', value=str(exc))
                row.setdefault('initialization', {'status': 'unknown', 'reason': row.get('value', '无法读取变量')})
                result.append(row)
                if len(result) >= 128:
                    return result, '每帧最多采集 128 个变量'
            block = block.superblock
        return result, '' if result else '没有可读的参数或局部变量；可能缺少调试符号'
    except Exception as exc:
        return result, str(exc)

class AutoValueCapture:
    def __init__(self):
        self.last_count = 0
        self.sequence = 0
        self.active = False
        self.seen = set()
        gdb.events.stop.connect(self.on_stop)

    def on_stop(self, event):
        if self.active:
            return
        self.active = True
        try:
            counters = capture_execute('monitor v.info n_errs_found')
            match = re.search(r'n_errs_found\s+(\d+)', counters)
            if not match:
                gdb.write('[AiValgrind] 无法读取错误计数，本次未采集：' + counters + '\n')
                return
            count = int(match.group(1))
            # A breakpoint, step or SIGINT must not relabel an old error as new.
            if count <= self.last_count:
                return
            message = capture_execute('monitor v.info last_error')
            if message.startswith('[unavailable:') or not message.strip():
                gdb.write('[AiValgrind] 无法读取本次错误，未采集：' + message + '\n')
                return
            identity = capture_identity(message)
            if identity in self.seen:
                self.last_count = count
                return
            snapshot = {'captured_at': datetime.now(timezone.utc).isoformat(),
                        'requested_error_id': _capture_config['requested_error_id'],
                        'matching_note': '这是本次运行的实际错误，不保证与选中的历史 XML 错误相同。',
                        'error_count': count, 'errors_since_previous_capture': count - self.last_count,
                        'valgrind_error': message, 'memory': memory_facts(message),
                        'backtrace': capture_execute('bt 16'), 'frames': [],
                        'limits': '当前停止线程，最多 16 帧、每帧 128 个变量；单个值最多 4096 字符。变量是当时可观察值，不自动推断数组下标或期望值。'}
            thread = gdb.selected_thread()
            snapshot['thread'] = list(thread.ptid) if thread else None
            if snapshot['memory'].get('address'):
                snapshot['address_details'] = capture_execute('monitor v.info location ' + snapshot['memory']['address'])
            frame = gdb.newest_frame()
            initialization_budget = {'queries': 256, 'bytes': 32768}
            for index in range(16):
                if frame is None:
                    break
                row = {'index': index}
                try:
                    row['function'] = frame.name() or '<unknown>'
                    sal = frame.find_sal()
                    row['file'] = sal.symtab.fullname() if sal.symtab else ''
                    row['line'] = sal.line
                    row['source_line'] = capture_source_line(row['file'], sal.line)
                    row['variables'], row['note'] = capture_variables(frame, initialization_budget)
                except Exception as exc:
                    row['note'] = str(exc)
                snapshot['frames'].append(row)
                try:
                    frame = frame.older()
                except Exception as exc:
                    snapshot['unwind_error'] = str(exc)
                    break
            snapshot['initialization_analysis'] = analyze_initialization_use(snapshot)
            self.sequence += 1
            folder = Path(_capture_config['directory'])
            base = folder / ('error-%04d' % self.sequence)
            manifest = folder / '.aivalgrind-files.json'
            # Register exact generated names before writing; unrelated files are never scanned.
            if manifest.exists():
                ownership = json.loads(manifest.read_text(encoding='utf-8'))
                for suffix in ('.json', '.txt', '.html'):
                    name = base.with_suffix(suffix).name
                    if name not in ownership['files']:
                        ownership['files'].append(name)
                manifest.write_text(json.dumps(ownership), encoding='utf-8')
            serialized = json.dumps(snapshot, ensure_ascii=False, indent=2)
            base.with_suffix('.json').write_text(serialized, encoding='utf-8')
            lines = ['AiValgrind 错误现场', snapshot['captured_at'], snapshot['matching_note'],
                     message, snapshot['memory'].get('explanation', '没有足够的内存块信息自动解释越界，请结合下方原始错误和变量值判断。'),
                     snapshot.get('address_details', ''), snapshot['backtrace'],
                     '\n初始化状态分析：' + snapshot['initialization_analysis']['note']]
            for finding in snapshot['initialization_analysis']['findings']:
                lines.append('[%s] #%s %s：%s' % ('错误' if finding['severity'] == 'error' else '警告', finding['frame'], finding['name'], finding['evidence']))
            line_details = []
            for item in snapshot['initialization_analysis']['line_variables']:
                line_details.append('同一行变量 #%s %s：%s%s（仅源码关联，不能确认是错误来源）' % (
                    item['frame'], item['name'], INIT_LABELS.get(item['status'], item['status']),
                    '' if item['complete'] else '；检查不完整'))
            lines.extend(line_details)
            for row in snapshot['frames']:
                lines.append('\n#%s %s %s:%s' % (row['index'], row.get('function', ''), row.get('file', ''), row.get('line', '')))
                if row.get('source_line'):
                    lines.append('  源码：' + row['source_line']['text'])
                for variable in row.get('variables', []):
                    lines.append('  %s %s (%s) = %s [%s]' % (variable['role'], variable['name'], variable.get('type', ''), variable['value'], variable['status']))
                    lines.append('    初始化状态：' + initialization_summary(variable['initialization']))
                if row.get('note'):
                    lines.append('  ' + row['note'])
            lines.append('\n' + snapshot['limits'])
            lines.append(snapshot['initialization_analysis']['limits'])
            readable = '\n'.join(lines)
            base.with_suffix('.txt').write_text(readable, encoding='utf-8')
            base.with_suffix('.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AiValgrind 错误现场</title><body><h1>内存错误现场</h1><pre style="white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6">' + html.escape(readable) + '</pre></body></html>', encoding='utf-8')
            self.last_count = count
            self.seen.add(identity)
            gdb.write('\n[AiValgrind] 已保存现场: ' + str(base.with_suffix('.html')) + '\n')
            gdb.write(snapshot['memory'].get('explanation', '') + '\n')
            gdb.write(snapshot['initialization_analysis']['note'] + '\n')
            for detail in line_details:
                gdb.write(detail + '\n')
            for finding in snapshot['initialization_analysis']['findings']:
                gdb.write('[%s] #%s %s：%s\n' % ('错误' if finding['severity'] == 'error' else '警告', finding['frame'], finding['name'], finding['evidence']))
            for row in snapshot['frames']:
                for variable in row.get('variables', []):
                    gdb.write('#%s %s = %s\n' % (row['index'], variable['name'], variable['value'][:256]))
                    gdb.write('  初始化状态：' + initialization_summary(variable['initialization']) + '\n')
            gdb.write('现场保持暂停；continue 继续，quit 退出。\n')
        except Exception as exc:
            gdb.write('[AiValgrind] 采集失败，现场保持暂停: ' + str(exc) + '\n')
        finally:
            self.active = False

_aivalgrind_capture = AutoValueCapture()
gdb.write('[AiValgrind] 自动变量采集已启用。\n')
'''
