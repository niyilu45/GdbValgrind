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
    def test_imported_main_preserves_host_paths_and_inc_package(self):
        checkout = Path(__file__).resolve().parents[1]
        for preload in (False, True):
            with self.subTest(preload=preload), tempfile.TemporaryDirectory() as folder:
                host = Path(folder)
                package = host / 'inc'
                package.mkdir()
                (package / '__init__.py').write_text('HOST = True', encoding='utf-8')
                (package / 'extra.py').write_text('VALUE = 42', encoding='utf-8')
                (host / 'plugin.py').write_text('VALUE = 17', encoding='utf-8')
                (host / 'errors.xml').write_text('<valgrindoutput/>', encoding='utf-8')
                script = """
import importlib.util
import sys
from pathlib import Path
checkout, host, preload = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == 'True'
if preload:
    sys.path.append(str(host))
    import inc
    original_inc = inc
before_path, before_meta = list(sys.path), list(sys.meta_path)
spec = importlib.util.spec_from_file_location('external_memory_tool', checkout / 'main.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
assert sys.path == before_path
assert sys.meta_path == before_meta
if not preload:
    assert 'inc' not in sys.modules
    sys.path.append(str(host))
import inc
from inc.extra import VALUE
import plugin
assert inc.HOST and VALUE == 42 and plugin.VALUE == 17
if preload:
    assert inc is original_inc
assert tool.main(['report', '--xml', str(host / 'errors.xml'), '--output', str(host / 'report.html')]) == 0
assert (host / 'report.html').is_file()
assert sys.modules['inc'] is inc
assert sys.meta_path == before_meta
"""
                result = subprocess.run([sys.executable, '-I', '-c', script, str(checkout), str(host), str(preload)],
                                        cwd=folder, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))

    def test_failed_import_removes_private_finder_and_partial_modules(self):
        checkout = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(checkout / 'inc', root / 'inc', ignore=shutil.ignore_patterns('__pycache__'))
            (root / 'inc' / 'core.py').write_text('raise RuntimeError("test failure")', encoding='utf-8')
            script = """
import runpy, sys
from pathlib import Path
before = list(sys.meta_path)
modules = set(sys.modules)
load = runpy.run_path(str(Path(sys.argv[1]) / 'inc' / 'bootstrap.py'))['load_checkout']
try:
    load()
except RuntimeError as exc:
    assert str(exc) == 'test failure'
else:
    raise AssertionError('expected failure')
assert sys.meta_path == before
assert not [name for name in set(sys.modules) - modules if name.startswith('_aivalgrind_')]
assert 'inc' not in sys.modules
"""
            result = subprocess.run([sys.executable, '-I', '-c', script, folder], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))

    def test_entry_points_ignore_stale_bytecode_without_deleting_it(self):
        checkout = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(checkout / 'inc', root / 'inc',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for entry in ('main.py',):
                shutil.copy2(checkout / entry, root / entry)
            caches = []
            for name, old in (
                ('workflow.py', b'def analyze_run(*args, **kwargs): pass\n'),
                ('api.py', b'from .workflow import refresh_capture_report\n'),
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
            broken = subprocess.run([sys.executable, '-c', 'import inc'],
                                    cwd=root, capture_output=True, text=True, timeout=20)
            self.assertIn('cannot import name', broken.stderr)
            self.assertIn('refresh_capture_report', broken.stderr)
            for entry in ('main.py',):
                with self.subTest(entry=entry):
                    result = subprocess.run([sys.executable, str(root / entry), '--help'],
                                            cwd=folder, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)
            # The real refresh path also reaches ordinary validation, not ImportError.
            result = subprocess.run([sys.executable, str(root / 'main.py'),
                                     'refresh-captures', '--output-dir', folder],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertIn('capture-updates.js', result.stderr)
            self.assertNotIn('ImportError', result.stderr)
            for path, original in caches:
                self.assertEqual(path.read_bytes(), original)
