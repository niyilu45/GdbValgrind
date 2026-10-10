from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import sqlite3
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from inc.store import ErrorStore, connect
from inc.paged import PagedReport
from inc.core import report_from_root
from tests.test_aivalgrind import error, frame


class PagedTests(unittest.TestCase):
    def test_worker_retries_transient_database_lock(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store=ErrorStore(Path(directory)/'results.sqlite3','errors.xml');cleanup.callback(store.close)
            store.accept(ET.fromstring(error('1')));store.commit()
            base=report_from_root(ET.Element('valgrindoutput'),'errors.xml')
            calls=[]
            def flaky(path):
                calls.append(path)
                if len(calls)==1:
                    raise sqlite3.OperationalError('database is locked')
                return connect(path)
            with patch('inc.paged.connect',side_effect=flaky), PagedReport(store.path,base,0) as live:
                deadline=time.monotonic()+3
                ready=False
                while time.monotonic()<deadline:
                    with connect(store.path) as db:
                        ready=bool(db.execute('SELECT ready FROM issues').fetchone()[0])
                    if ready:
                        break
                    time.sleep(0.02)
                self.assertTrue(ready)
                self.assertTrue(live.worker.is_alive())

    def test_source_is_visible_before_slow_blame_finishes(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            (Path(directory)/'demo.c').write_text('\n'.join('int value = %d;' % n for n in range(30)))
            store=ErrorStore(Path(directory)/'results.sqlite3','errors.xml');cleanup.callback(store.close)
            store.accept(ET.fromstring(error('1')));store.commit()
            base=report_from_root(ET.Element('valgrindoutput'),'errors.xml');base['project']=directory
            entered=threading.Event()
            def slow_blame(*args, **kwargs):
                entered.set()
                while True:
                    kwargs['checkpoint']()
                    time.sleep(0.01)
            with patch('inc.core.source_blame',slow_blame), PagedReport(store.path,base,0) as live:
                self.assertTrue(entered.wait(2))
                with connect(store.path) as db:
                    identity=db.execute('SELECT id FROM issues').fetchone()[0]
                client=http.client.HTTPConnection('127.0.0.1',live.server.server_port,timeout=3)
                client.request('GET','/api/issue?id='+identity)
                response=client.getresponse();data=json.loads(response.read());client.close()
                self.assertEqual(response.status,200)
                self.assertFalse(data['ready'])
                self.assertIn('int value', data['report']['source_snippets'][0][0]['text'])

    def test_paging_filters_and_slow_source_do_not_block_ingestion(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store=ErrorStore(Path(directory)/'results.sqlite3','errors.xml')
            cleanup.callback(store.close)
            for n in range(205):
                store.accept(ET.fromstring(error(str(n),frames=frame(line=n+1))))
            store.commit({'state':'running'})
            base=report_from_root(ET.Element('valgrindoutput'),'errors.xml',xml_complete=False)
            base['project']=directory
            entered=threading.Event()
            def slow(sources, frame, with_blame=True):
                if threading.current_thread().name != 'aivalgrind-source':
                    return
                entered.set()
                while True:
                    sources.progress('blocked test')
                    time.sleep(0.01)
            with patch('inc.paged.Sources.enrich',slow), PagedReport(store.path,base,0) as live:
                def get(path):
                    client=http.client.HTTPConnection('127.0.0.1',live.server.server_port,timeout=3)
                    client.request('GET',path)
                    response=client.getresponse();body=response.read();client.close()
                    self.assertEqual(response.status,200,body)
                    return json.loads(body)
                self.assertTrue(entered.wait(2))
                data=get('/api/issues?offset=100')
                self.assertEqual(len(data['errors']),100)
                self.assertEqual(data['errors'][0]['seq'],101)
                self.assertNotIn('stacks',data['errors'][0])
                self.assertEqual(get('/api/issues?file=missing.c')['total'],0)
                store.accept(ET.fromstring(error('new',kind='InvalidRead')));store.commit()
                self.assertEqual(get('/api/issues?kind=InvalidRead')['total'],1)
                detail=get('/api/issue?id='+data['errors'][0]['id'])
                self.assertFalse(detail['ready'])
                self.assertTrue(detail['report']['errors'][0]['stacks'])
            self.assertFalse(live.worker.is_alive())

    def test_author_only_innermost_and_reachable_count_excluded(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            store=ErrorStore(Path(directory)/'results.sqlite3','errors.xml');cleanup.callback(store.close)
            store.accept(ET.fromstring(error('1',frames=frame()+frame(line=30))))
            store.accept(ET.fromstring(error('2',kind='Leak_StillReachable')));store.commit()
            base=report_from_root(ET.Element('valgrindoutput'),'errors.xml')
            base['project']=directory
            def enrich(sources,f,with_blame=True):
                f['source']=[{'number':int(f['line']),'text':'x','blame':{'author':'Alice' if f['line']=='15' else 'Parent'}}]
            with patch('inc.paged.Sources.enrich',enrich), PagedReport(store.path,base,0) as live:
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    with connect(store.path) as db:
                        if db.execute('SELECT count(*) FROM issues WHERE author_done=2').fetchone()[0]==2:
                            break
                    time.sleep(0.02)
                client=http.client.HTTPConnection('127.0.0.1',live.server.server_port,timeout=3)
                client.request('GET','/api/issues?author=author%3AAlice')
                result=json.loads(client.getresponse().read());client.close()
                self.assertEqual(result['total'],2)
                self.assertEqual(result['authors'],[{'author':'Alice','n':1}])
