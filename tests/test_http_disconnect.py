import unittest
from unittest.mock import Mock

from inc.core import Handler
from inc.paged import PagedHandler


class DisconnectTests(unittest.TestCase):
    def test_disconnected_response_does_not_raise_or_retry(self):
        for cls in (Handler, PagedHandler):
            for stage in ('end_headers', 'write'):
                for failure in (BrokenPipeError(32, 'broken pipe'), ConnectionResetError(),
                                ConnectionAbortedError(), TimeoutError()):
                    with self.subTest(handler=cls, stage=stage, failure=type(failure)):
                        handler = object.__new__(cls)
                        handler.send_response = Mock()
                        handler.send_header = Mock()
                        handler.end_headers = Mock()
                        handler.wfile = Mock()
                        target = handler.end_headers if stage=='end_headers' else handler.wfile.write
                        target.side_effect = failure
                        handler.reply(200, {'message':'response'})
                        self.assertTrue(handler.close_connection)
                        handler.send_response.assert_called_once_with(200)
                        target.assert_called_once()

    def test_unrelated_io_errors_are_not_hidden(self):
        handler = object.__new__(Handler)
        handler.send_response = Mock(side_effect=OSError('unexpected error'))
        with self.assertRaises(OSError):
            handler.reply(200, {})
