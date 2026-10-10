from contextlib import ExitStack
import json
import tempfile
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from inc.store import ErrorStore, connect, issue_data, load_replay
from inc.xmlstream import XMLStream
from inc.core import report_from_root
from tests.test_aivalgrind import error, frame, xml


class StoreTests(unittest.TestCase):
    def test_incremental_counts_and_dedup_match_offline(self):
        errors = error('1') + error('2',frames=frame(ip='0x999')) + error('1') + error('3',kind='InvalidRead')
        counts = '<pair><unique>1</unique><count>7</count></pair><pair><unique>2</unique><count>3</count></pair><pair><unique>3</unique><count>2</count></pair>'
        raw = xml(errors,counts).encode()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store = ErrorStore(Path(directory)/'results.sqlite3','errors.xml')
            cleanup.callback(store.close)
            stream = XMLStream(sink=store.accept)
            for start in range(0,len(raw),31):
                stream.feed(raw[start:start+31])
            stream.finish();store.commit()
            self.assertEqual(stream.root.findall('error'),[])
            self.assertEqual(stream.root.findall('errorcounts/pair'),[])
            reference = report_from_root(ET.fromstring(raw),'errors.xml')
            with connect(store.path) as db:
                rows = [issue_data(row) for row in db.execute('SELECT * FROM issues ORDER BY seq')]
            for item in reference['errors']:
                item.pop('unique_ids')
            self.assertEqual(rows,reference['errors'])
            self.assertEqual(rows[0]['records'], 2)
            self.assertEqual(rows[0]['stacks'][0]['frames'][0]['ip'], '0x111')
            self.assertTrue(store.summary(stream.complete)['counts_complete'])
            self.assertEqual(store.summary()['occurrences'],12)
            store.commit(store.summary(stream.complete))
            replay=load_replay(store.path,'errors.xml')
            self.assertEqual(replay['errors'],reference['errors'])

    def test_partial_counts_before_errors_and_empty_uid(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store=ErrorStore(Path(directory)/'results.sqlite3','errors.xml')
            cleanup.callback(store.close)
            store.accept(ET.fromstring('<pair><unique>1</unique><count>9</count></pair>'))
            store.accept(ET.fromstring(error('1')))
            store.accept(ET.fromstring(error('')))
            self.assertEqual(store.summary()['occurrences'],10)
            self.assertFalse(store.summary()['counts_complete'])
            store.commit()

    def test_retained_xml_does_not_grow_with_history(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store=ErrorStore(Path(directory)/'results.sqlite3','errors.xml')
            cleanup.callback(store.close)
            stream=XMLStream(sink=store.accept)
            stream.feed(b'<valgrindoutput>')
            for n in range(500):
                stream.feed(error(str(n)).encode())
            store.commit()
            self.assertEqual(len(stream.root),1)
            self.assertEqual(store.summary()['locations'],1)
            self.assertEqual(store.summary()['occurrences'],500)
