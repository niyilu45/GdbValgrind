import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from inc.program_output import ProgramOutput


@unittest.skipUnless(os.name == 'posix', '需要 POSIX 伪终端')
class ProgramOutputTests(unittest.TestCase):
    def test_line_buffered_output_arrives_before_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            out, err = Path(directory) / 'out', Path(directory) / 'err'
            with out.open('wb') as stdout, err.open('wb') as stderr:
                with ProgramOutput(stdout, stderr) as transport:
                    child = subprocess.Popen([sys.executable, '-c',
                        'import sys,time; print("progress",sys.stdout.isatty()); print("warning",file=sys.stderr); time.sleep(10)'],
                        stdout=transport.stdout, stderr=transport.stderr)
                    try:
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            if b'progress True\n' in out.read_bytes() and b'warning\n' in err.read_bytes():
                                break
                            time.sleep(0.02)
                        self.assertIsNone(child.poll())
                        self.assertIn(b'progress True\n', out.read_bytes())
                        self.assertIn(b'warning\n', err.read_bytes())
                    finally:
                        child.terminate()
                        child.wait(timeout=3)
                self.assertIsNone(transport.thread)
                self.assertEqual(transport.pairs, [])
