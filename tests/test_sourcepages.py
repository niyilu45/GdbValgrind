import http.client
from pathlib import Path
import tempfile
import unittest

from inc.core import save_report, render_html
from inc.live import LiveReport
from inc.sourcepages import source_page


class SourcePageTests(unittest.TestCase):
    def test_offline_only_paths_and_no_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'a.c'
            source.write_text('UNIQUE_SOURCE_TEXT')
            report = {'errors': [], 'source_files': {'0123456789abcdef': {'path': str(source)}}}
            output = Path(folder) / 'report.html'
            save_report(report, output)
            content = output.read_text(encoding='utf-8')
            self.assertNotIn('UNIQUE_SOURCE_TEXT', content)
            self.assertIn('file:///', content)
            self.assertFalse(output.with_name('report.html.sources').exists())

    def test_http_reads_current_source_and_handles_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'a.c'
            source.write_text('UNIQUE_SOURCE_TEXT')
            key = '0123456789abcdef'
            report = {'errors': [], 'project': folder, 'source_files': {key: {'path': str(source)}}}
            with LiveReport(report, 0) as live:
                def get(path):
                    client = http.client.HTTPConnection('127.0.0.1', live.server.server_port, timeout=3)
                    client.request('GET', path)
                    response = client.getresponse()
                    result = response.status, response.read()
                    client.close()
                    return result
                self.assertNotIn(b'UNIQUE_SOURCE_TEXT', get('/')[1])
                self.assertNotIn(b'UNIQUE_SOURCE_TEXT', get('/api/live?revision=-1')[1])
                self.assertIn(b'UNIQUE_SOURCE_TEXT', get('/source/' + key + '.html')[1])
                source.write_text('<script>CHANGED</script>')
                status, body = get('/source/' + key + '.html')
                self.assertEqual(status, 200)
                self.assertIn(b'CHANGED', body)
                self.assertNotIn(b'<script>', body)
                self.assertEqual(get('/source/../../a.c')[0], 404)
                source.unlink()
                self.assertEqual(get('/source/' + key + '.html')[0], 404)
