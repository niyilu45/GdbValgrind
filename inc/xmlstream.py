"""Incremental Valgrind XML reader; only completed records are exposed."""
import xml.etree.ElementTree as ET
from pathlib import Path


class XMLStream:
    def __init__(self):
        self.parser = ET.XMLPullParser(events=('start', 'end'))
        self.root = ET.Element('valgrindoutput')
        self.counts = ET.SubElement(self.root, 'errorcounts')
        self.stack = []
        self.started = False
        self.complete = False
        self.tail = b''
        self.parse_error = ''

    def feed(self, data):
        scan = self.tail + data
        if b'<!DOCTYPE' in scan.upper() or b'<!ENTITY' in scan.upper():
            raise ValueError('不支持包含 DTD / ENTITY 的 XML')
        self.tail = scan[-16:]
        # Valgrind emits UTF-8. Reject UTF-16/32 rather than bypass DTD checks.
        if b'\x00' in data:
            raise ValueError('Valgrind XML 必须使用 UTF-8 编码')
        self.parser.feed(data)
        self._drain()

    def _drain(self):
        for event, node in self.parser.read_events():
            if event == 'start':
                if not self.started:
                    if node.tag != 'valgrindoutput':
                        raise ValueError('输入不是 Valgrind XML')
                    self.started = True
                self.stack.append(node)
                continue
            if len(self.stack) == 3 and self.stack[-2].tag == 'errorcounts' and node.tag == 'pair':
                self.counts.append(node)
                self.stack[-2].remove(node)
            elif len(self.stack) == 2:
                if node.tag != 'errorcounts':
                    self.root.append(node)
                self.stack[0].remove(node)
            elif len(self.stack) == 1:
                self.complete = True
            self.stack.pop()

    def finish(self, allow_partial=True):
        try:
            self.parser.close()
            self._drain()
        except ET.ParseError as exc:
            # Expat EOF errors: no element, unfinished token/UTF-8/CDATA.
            if not allow_partial or exc.code not in (3, 5, 6, 20):
                raise ValueError('XML 格式无效: ' + str(exc)) from exc
            self.parse_error = str(exc)
        if not self.started:
            raise ValueError('XML 尚未包含 valgrindoutput 根节点')
        return self.root


def read_xml(path, allow_partial=True):
    stream = XMLStream()
    try:
        with Path(path).open('rb') as file:
            while True:
                data = file.read(65536)
                if not data:
                    break
                stream.feed(data)
        stream.finish(allow_partial)
    except ET.ParseError as exc:
        raise ValueError('XML 格式无效: ' + str(exc)) from exc
    return stream
