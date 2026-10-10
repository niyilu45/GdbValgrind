from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from inc import core
from tests.test_aivalgrind import error, frame, xml


class ParallelReportTests(unittest.TestCase):
    def test_repeated_positions_are_enriched_once_without_merging_stacks(self):
        frames = [{'file':'demo.c','line':'15','fn':'function%d' % i,'ip':hex(i)} for i in range(100000)]
        report = {'errors':[{'stacks':[{'frames':frames}]}], 'source_files':{}}
        calls = []
        def enrich(sources, frame):
            calls.append(frame)
            frame['source'] = [{'number':15,'text':'int value;'}]
        with tempfile.TemporaryDirectory() as folder, patch.object(core.Sources, 'enrich', enrich):
            core.enrich_parallel(report, folder, 4)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(frames), 100000)
        self.assertEqual(frames[-1]['fn'], 'function99999')
        self.assertEqual(frames[-1]['ip'], hex(99999))
        self.assertIs(frames[0]['source'], frames[-1]['source'])

    def test_reuse_skips_completed_frames_but_refreshes_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'demo.c'
            source.write_text('int x;\n' * 30)
            f = {'file':'demo.c', 'line':'15'}
            with patch('inc.core.source_blame', return_value={15:{'text':'int x;', 'author':'Alice'}}):
                core.Sources(root).enrich(f)
            report = {'errors':[{'stacks':[{'frames':[f]}]}], 'source_files':{}}
            with patch.object(core.Sources, 'enrich') as enrich:
                core.enrich_parallel(report, root, 4, reuse=True)
                enrich.assert_not_called()
                self.assertIn(f['source_file_id'], report['source_files'])
            source.write_text('int changed;\n' * 30)
            with patch('inc.core.source_blame', return_value={}) as blame:
                core.enrich_parallel(report, root, 4, reuse=True)
                blame.assert_called_once()
                self.assertEqual(f['source'][0]['text'], 'int changed;')

    def test_parallel_report_matches_serial(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'demo.c').write_text('\n'.join('int x%d;' % n for n in range(60)))
            path=root/'errors.xml'
            path.write_text(xml(''.join(error(str(i),frames=frame(line=i+1)) for i in range(20))))
            with patch('inc.core.source_blame',return_value={}):
                serial=core.load_report(path,root)
                stages=[]
                parallel=core.load_report(path,root,workers=4,progress=stages.append)
            self.assertEqual(serial,parallel)
            self.assertTrue(any('20 / 20' in stage for stage in stages))

    def test_cancel_stops_pending_workers(self):
        report={'errors':[{'stacks':[{'frames':[{'file':'a.c'} for _ in range(30)]}]}],'source_files':{}}
        started=threading.Event()
        active=[0];peak=[0];lock=threading.Lock()
        def slow(sources,frame):
            with lock:
                active[0]+=1;peak[0]=max(peak[0],active[0])
            started.set()
            try:
                while True:
                    sources.progress('waiting')
                    time.sleep(.01)
            finally:
                with lock:active[0]-=1
        def progress(text):
            if started.is_set():raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as folder, patch.object(core.Sources,'enrich',slow):
            with self.assertRaises(KeyboardInterrupt):
                core.enrich_parallel(report,folder,4,progress)
        self.assertEqual(active[0],0)
        self.assertLessEqual(peak[0],4)
