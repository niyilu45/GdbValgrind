from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from inc.watchdog import CollectionWatchdog


class WatchdogTests(unittest.TestCase):
    def test_stalled_stage_records_stacks_and_thread_stops(self):
        written = threading.Event()
        contents = []
        def write(path, text, **kwargs):
            contents.append(text)
            written.set()
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(Path, 'write_text', write):
                with CollectionWatchdog(Path(folder) / 'diagnostics.log', timeout=0.01) as watchdog:
                    watchdog.mark('正在解析 XML')
                    self.assertTrue(written.wait(3))
            self.assertFalse(watchdog.thread.is_alive())
            self.assertIn('正在解析 XML', contents[0])
            self.assertIn('MainThread', contents[0])
