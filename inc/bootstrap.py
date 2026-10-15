"""Load checkout sources without changing the host's public import namespace."""
import hashlib
import importlib
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
    def __init__(self, root, package):
        self.root = root
        self.package = package

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.package and not fullname.startswith(self.package + '.'):
            return None
        relative = fullname.split('.')[1:]
        location = self.root.joinpath(*relative)
        package = location / '__init__.py'
        source = package if package.is_file() else location.with_suffix('.py')
        if not source.is_file():
            return None
        loader = CheckoutSourceLoader(fullname, str(source))
        return importlib.util.spec_from_file_location(
            fullname, str(source), loader=loader,
            submodule_search_locations=[str(location)] if source == package else None)


def load_checkout():
    root = Path(__file__).resolve().parent
    # Share the library only when the host explicitly imported this exact inc.
    existing = sys.modules.get('inc')
    existing_file = getattr(existing, '__file__', None)
    shared = existing_file and Path(existing_file).resolve() == root / '__init__.py'
    name = 'inc' if shared else '_aivalgrind_' + hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:20]
    finder = CheckoutFinder(root, name)
    before = set(sys.modules)
    # This finder recognizes only our package, and is always removed. Future
    # relative imports use the package's __path__, never the host's sys.path.
    sys.meta_path.insert(0, finder)
    try:
        modules = {'library': importlib.import_module(name)}
        for component in ('core', 'collect', 'workflow', 'versions', 'runtime_paths', 'terminal'):
            modules[component] = importlib.import_module('.' + component, name)
        return modules
    except BaseException:
        if not shared:
            for key in set(sys.modules) - before:
                if key == name or key.startswith(name + '.'):
                    sys.modules.pop(key, None)
        raise
    finally:
        sys.meta_path.remove(finder)
