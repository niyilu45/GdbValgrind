import http.client
import json
import unittest

from inc.core import report_from_root
from inc.xmlstream import XMLStream
from inc.live import LiveReport


class LiveReportTests(unittest.TestCase):
    def test_snapshot_update_readonly_and_cleanup(self):
        stream = XMLStream()
        report = report_from_root(stream.root, 'errors.xml', xml_complete=False)
        live = LiveReport(report, 0)
        with live:
            def request(path, host=None, method='GET'):
                client = http.client.HTTPConnection('127.0.0.1', live.server.server_port, timeout=3)
                client.request(method, path, headers={'Host': host} if host else {})
                response = client.getresponse()
                data = response.read()
                client.close()
                return response.status, data
            status, body = request('/')
            self.assertEqual(status, 200)
            self.assertIn(b'"live": true', body)
            initial = json.loads(request('/api/live?revision=-1')[1])
            self.assertEqual(initial['report']['errors'], [])
            stream.feed(b'<valgrindoutput><error><unique>1</unique><kind>InvalidRead</kind><what>Invalid read</what></error><error>')
            live.update(report_from_root(stream.root, 'errors.xml', xml_complete=False), 'running')
            updated = json.loads(request('/api/live?revision=0')[1])
            self.assertEqual(len(updated['report']['errors']), 1)
            self.assertNotIn('report', json.loads(request('/api/live?revision=1')[1]))
            self.assertEqual(request('/', 'evil.example')[0], 403)
            self.assertEqual(request('/api/debug', method='POST')[0], 403)
        self.assertFalse(live.thread.is_alive())
        self.assertEqual(live.server.socket.fileno(), -1)
