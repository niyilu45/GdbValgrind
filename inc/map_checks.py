"""Read-only, bounded map inspection embedded in GDB."""

MAP_CHECK_SCRIPT = r'''
def source_is_map(value):
    return bool(re.search(r'(?:^|::)(?:map|unordered_map)\s*<', str(value.type.strip_typedefs())))

def source_map_pairs(value):
    printer = gdb.default_visualizer(value) if hasattr(gdb, 'default_visualizer') else None
    if printer is not None and printer.display_hint() == 'map':
        children = iter(printer.children())
        for _ in range(128):
            try:
                _, key = next(children)
            except StopIteration:
                return
            try:
                _, item = next(children)
            except StopIteration:
                raise ValueError('map 可视化器返回了不完整的键值对')
            yield key, item
        raise ValueError('达到 128 个键值对查询上限，不能认定键不存在')
    # libstdc++ std::map fallback, without loading packages or calling inferior
    # methods. Other ABIs require a registered map pretty-printer.
    if not re.search(r'(?:^|::)map\s*<', str(value.type.strip_typedefs())):
        raise ValueError('此字典布局没有可用的 GDB map 可视化器')
    tree = value['_M_t']
    pair_type = tree.type.strip_typedefs().template_argument(1)
    node_type = gdb.lookup_type('std::_Rb_tree_node<%s>' % pair_type)
    root = tree['_M_impl']['_M_header']['_M_parent']
    pending, seen = [], set()
    while int(root) or pending:
        while int(root):
            address = int(root)
            if address in seen:
                raise ValueError('map 节点存在循环，停止读取')
            if len(seen) >= 128:
                raise ValueError('达到 128 个节点查询上限，不能认定键不存在')
            seen.add(address)
            node = root.cast(node_type.pointer()).dereference()
            pending.append(node)
            root = node['_M_left']
        node = pending.pop()
        try:
            pair = node['_M_value_field']
        except Exception as exc:
            if disconnected(str(exc)):
                raise CaptureDisconnected(str(exc))
            pair = node['_M_storage'].address.cast(pair_type.pointer()).dereference()
        yield pair['first'], pair['second']
        root = node['_M_right']

def source_map_key(value):
    if isinstance(value, (int, str)):
        return value
    kind = value.type.strip_typedefs()
    if kind.code in (gdb.TYPE_CODE_INT, gdb.TYPE_CODE_ENUM, gdb.TYPE_CODE_BOOL, gdb.TYPE_CODE_CHAR):
        return int(value)
    if 'basic_string<' in str(kind) or str(kind) == 'std::string':
        length = int(value['_M_string_length'])
        if not 0 <= length <= 4096:
            raise ValueError('字符串键超过 4096 字节或长度异常')
        return value['_M_dataplus']['_M_p'].string(length=length)
    raise ValueError('暂不支持此 map 键类型；不调用用户定义的比较运算')

def source_map_lookup(value, key):
    expected = source_map_key(key)
    comparator = str(value.type.strip_typedefs().template_argument(2))
    if not any(name in comparator for name in ('std::less<', 'std::greater<', 'std::equal_to<')):
        raise ValueError('自定义 map 比较器语义未知，不猜测键是否相等')
    for existing, item in source_map_pairs(value):
        if source_map_key(existing) == expected:
            return item
    raise ValueError('map 中未找到键 %s；未执行 operator[]，不会插入新键' % expected)

def source_map_summary(value):
    rows = []
    for key, item in source_map_pairs(value):
        if len(rows) == 16:
            rows.append('…其余键值对未展开')
            break
        kind = item.type.strip_typedefs().code
        rendered = ('<复合对象，需指定成员>' if kind in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_ARRAY, gdb.TYPE_CODE_UNION)
                    else item.format_string(raw=True)[:256])
        rows.append(str(source_map_key(key)) + ' => ' + rendered)
    return 'map 键值预览：' + ('；'.join(rows) if rows else '空容器')
'''
