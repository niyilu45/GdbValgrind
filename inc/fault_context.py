"""Read-only instruction evidence for the exact stopped thread/frame."""
FAULT_CONTEXT_SCRIPT = r'''
def capture_fault_context(frame, memory, precision):
    result = {'version': 1, 'operands': [], 'precision': precision,
              'note': '指令地址匹配是证据，不等于已证明源码变量或最初破坏内存的原因。'}
    if memory.get('access') not in ('read', 'write'):
        return result
    # Probe the reported access itself, separately from source-variable bytes.
    # No target calls, memory writes or resume/step commands are used.
    try:
        width, address = int(memory.get('access_bytes', 0)), int(memory['address'], 16)
        if width > 0:
            count = min(width, 4096)
            raw = capture_execute('monitor get_vbits '+hex(address)+' '+str(count))
            result['access_check'] = {'address':hex(address), 'requested_bytes':width,
                                      'queried_bytes':count, 'raw':raw[:16384]}
            try:
                bits = parse_vbits(raw, count)
                result['access_check'].update(complete=count==width,
                    unaddressable_offsets=[i for i,v in enumerate(bits) if v is None],
                    undefined_offsets=[i for i,v in enumerate(bits) if v is not None and v != 0])
            except Exception as exc:
                result['access_check']['error'] = str(exc)
    except Exception as exc:
        if disconnected(str(exc)):
            raise CaptureDisconnected(str(exc))
        result['access_check_error'] = str(exc)
    try:
        pc = int(frame.pc())
        arch = frame.architecture()
        result['architecture'] = arch.name()
        result['pc'] = hex(pc)
        # Select the faulting frame, not whichever frame a previous query left.
        frame.select()
        result['registers'] = capture_execute('info registers')[:16384]
        result['instruction'] = capture_execute('x/i $pc')[:2048]
        if 'i386' not in arch.name() and 'x86' not in arch.name():
            result['note'] += ' 当前架构仅保存原始指令和寄存器，未自动解码寻址。'
            return result
        flavor = gdb.parameter('disassembly-flavor')
        try:
            gdb.execute('set disassembly-flavor att', to_string=True)
            instructions = arch.disassemble(pc, count=1)
        finally:
            gdb.execute('set disassembly-flavor '+str(flavor), to_string=True)
        if not instructions or int(instructions[0]['addr']) != pc:
            return result
        instruction = instructions[0]
        asm = instruction['asm']
        result['instruction'] = asm
        # Only ordinary explicit load/store forms. String, gather, masked,
        # atomic and implicit-stack operands must not be guessed.
        mnemonic = asm.split()[0]
        if not re.fullmatch(r'(?:mov(?:[bwlq]|ss|sd|ups|upd|aps|apd|dqu|dqa|z[bw][wlq]|s[bwl][wlq])?|cmp[bwlq]?|test[bwlq]?)', mnemonic):
            result['note'] += ' 此指令不在保守地址解码范围内。'
            return result
        address_bits = 64 if '64' in arch.name() else 32
        pattern = r'(?P<disp>-?(?:0x[0-9a-fA-F]+|\d+))?\(%(?P<base>[a-z0-9]+)(?:,%(?P<index>[a-z0-9]+)(?:,(?P<scale>[1248]))?)?\)'
        for match in re.finditer(pattern, asm):
            if match.start() and asm[match.start()-1] == ':':
                continue  # FS/GS or other segment bases are not accounted for.
            base_name, index_name = match['base'], match['index']
            if not re.fullmatch(r'(?:r(?:ax|bx|cx|dx|si|di|bp|sp|ip|[89]|1[0-5])|e(?:ax|bx|cx|dx|si|di|bp|sp|ip))', base_name):
                continue
            base = pc+int(instruction['length']) if base_name in ('rip','eip') else int(frame.read_register(base_name))
            index = int(frame.read_register(index_name)) if index_name else 0
            displacement = int(match['disp'], 0) if match['disp'] else 0
            scale = int(match['scale'] or 1)
            bits = 32 if base_name.startswith('e') else address_bits
            address = (base+index*scale+displacement) % (1 << bits)
            actual = int(memory['address'],16) if memory.get('address') else None
            trusted = precision.get('register_precision') in ('allregs-at-each-insn','allregs-at-mem-access')
            result['operands'].append({'operand': match.group(), 'base_register': base_name,
                'base_value': hex(base), 'index_register': index_name, 'index_value': index,
                'scale': scale, 'displacement': displacement, 'address': hex(address),
                'matches_fault_address': address == actual, 'registers_precise': trusted})
    except Exception as exc:
        if disconnected(str(exc)):
            raise CaptureDisconnected(str(exc))
        result['error'] = str(exc)
    return result
'''
