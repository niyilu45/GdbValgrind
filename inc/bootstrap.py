"""Load this checkout's inc sources without trusting timestamp-based bytecode.

Executed by the CLI entry points before importing inc (including __init__).
Only our own package is affected; standard and third-party imports are unchanged.
"""
import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path
import sys


class CheckoutSourceLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        filename = self.get_filename(fullname)
        return self.source_to_code(self.get_data(filename), filename)


class CheckoutFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root):
        self.aivalgrind_source_root = root

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'inc' and not fullname.startswith('inc.'):
            return None
        relative = fullname.split('.')[1:]
        location = self.aivalgrind_source_root.joinpath(*relative)
        package = location / '__init__.py'
        source = package if package.is_file() else location.with_suffix('.py')
        if not source.is_file():
            return None
        loader = CheckoutSourceLoader(fullname, str(source))
        return importlib.util.spec_from_file_location(
            fullname, str(source), loader=loader,
            submodule_search_locations=[str(location)] if source == package else None)


root = Path(__file__).resolve().parent
if not any(getattr(finder, 'aivalgrind_source_root', None) == root for finder in sys.meta_path):
    sys.meta_path.insert(0, CheckoutFinder(root))
