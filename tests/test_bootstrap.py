"""Exercise real entry points with stale but timestamp-valid package bytecode."""
from pathlib import Path
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest


class BootstrapTests(unittest.TestCase):
    def test_entry_points_ignore_stale_bytecode_without_deleting_it(self):
        checkout = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(checkout / 'inc', root / 'inc',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for entry in ('aivalgrind.py', 'main.py'):
                shutil.copy2(checkout / entry, root / entry)
            caches = []
            for name, old in (
                ('workflow.py', b'def analyze_run(*args, **kwargs): pass\n'),
                ('cli.py', b'from .workflow import refresh_capture_report\n'),
            ):
                path = root / 'inc' / name
                current = path.read_bytes()
                stamp = int(path.stat().st_mtime)
                path.write_bytes(old + b' ' * (len(current) - len(old)))
                os.utime(path, (stamp, stamp))
                cached = Path(py_compile.compile(str(path), doraise=True,
                              invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
                caches.append((cached, cached.read_bytes()))
                path.write_bytes(current)
                os.utime(path, (stamp, stamp))
            broken = subprocess.run([sys.executable, '-c', 'import inc.cli'],
                                    cwd=root, capture_output=True, text=True, timeout=20)
            self.assertIn('cannot import name', broken.stderr)
            self.assertIn('refresh_capture_report', broken.stderr)
            for entry in ('aivalgrind.py', 'main.py'):
                with self.subTest(entry=entry):
                    result = subprocess.run([sys.executable, str(root / entry), '--help'],
                                            cwd=folder, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)
            # The real refresh path also reaches ordinary validation, not ImportError.
            result = subprocess.run([sys.executable, str(root / 'aivalgrind.py'),
                                     'refresh-captures', '--output-dir', folder],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertIn('capture-updates.js', result.stderr)
            self.assertNotIn('ImportError', result.stderr)
            for path, original in caches:
                self.assertEqual(path.read_bytes(), original)
