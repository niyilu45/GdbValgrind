from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from inc.core import load_report, render_html


@unittest.skipUnless(shutil.which('node'), '需要 Node.js 运行报告 JavaScript 回归测试')
class ReportUITests(unittest.TestCase):
    def test_exported_report_interactions(self):
        root = Path(__file__).resolve().parents[1]
        report = load_report(root / 'examples/sample.xml', root)
        with tempfile.TemporaryDirectory() as directory:
            html = Path(directory) / 'report.html'
            html.write_text(render_html(report), encoding='utf-8')
            result = subprocess.run(['node', str(root / 'tests/report_ui_test.js'), str(html)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
