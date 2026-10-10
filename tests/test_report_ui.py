from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from inc.core import load_report, render_html


@unittest.skipUnless(shutil.which('node'), '需要 Node.js 运行报告 JavaScript 回归测试')
class ReportUITests(unittest.TestCase):
    def test_complete_legacy_report_upgrade_and_capture_switching(self):
        import json
        from inc.workflow import save_combined_report, refresh_capture_report
        root=Path(__file__).resolve().parents[1]
        report=load_report(root/'examples/sample.xml',root)
        for i,error in enumerate(report['errors']):
            for fi,frame in enumerate(error['stacks'][0]['frames']):
                frame['source']=[{'number':frame.get('line'), 'text':'use(owner_%d_%d);'%(i,fi)}]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            base=render_html(report,source_base=None)
            old='<section id="capture-results"><section>old result</section></section><script>// aiv-stack-captures-v1\nthrow Error("LEGACY_WRONG_VARIABLE");</script>'
            (folder/'full-report.html').write_text(base.replace('</body>',old+'</body>'),encoding='utf-8')
            session=folder/'captures'/'session-test';session.mkdir(parents=True)
            for i,error in enumerate(report['errors']):
                frames=[]
                for fi,frame in enumerate(error['stacks'][0]['frames']):
                    frames.append({'file':frame.get('local_file') or frame.get('dir','')+'/'+frame.get('file',''),
                        'line':frame.get('line'), 'function':frame.get('fn'), 'index':fi,
                        'source_line':{'text':'use(owner_%d_%d);'%(i,fi),'identifiers':'use(owner_%d_%d);'%(i,fi)},
                        'variables':[{'name':'owner_%d_%d'%(i,fi),'value':str(i*100+fi),'status':'available'}]})
                snapshot={'valgrind_error':error['what'],'association':{'status':'verified'},'frames':frames}
                (session/('error-%d.json'%i)).write_text(json.dumps(snapshot),encoding='utf-8')
                (session/('error-%d.txt'%i)).write_text('capture',encoding='utf-8')
            save_combined_report(report,folder,base_html=base,replay_id='test-run')
            refresh_capture_report(folder)
            content=(folder/'full-report.html').read_text(encoding='utf-8')
            self.assertEqual(content.count('// aiv-stack-captures-v'),1)
            result=subprocess.run(['node',str(root/'tests/full_capture_ui_test.js'),str(folder/'full-report.html'),str(folder/'capture-updates.js')],capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_paged_report_interactions(self):
        root = Path(__file__).resolve().parents[1]
        report = {'errors': [], 'xml':'errors.xml','paged':True, 'finished':False}
        with tempfile.TemporaryDirectory() as directory:
            html = Path(directory) / 'report.html'
            html.write_text(render_html(report), encoding='utf-8')
            result = subprocess.run(['node', str(root / 'tests/paged_ui_test.js'), str(html)], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exported_report_interactions(self):
        root = Path(__file__).resolve().parents[1]
        report = load_report(root / 'examples/sample.xml', root)
        with tempfile.TemporaryDirectory() as directory:
            html = Path(directory) / 'report.html'
            html.write_text(render_html(report), encoding='utf-8')
            result = subprocess.run(['node', str(root / 'tests/report_ui_test.js'), str(html)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
