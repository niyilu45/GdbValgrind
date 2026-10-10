"""Clean only explicitly owned output files; never recursively remove a directory."""
import json
import re
from pathlib import Path

MANIFEST = '.aivalgrind-files.json'
COLLECTION_FILES = ['errors.xml', 'run.json', 'status.json', 'status.json.tmp',
                    'program.log', 'launcher.log', 'valgrind.log', 'report.html', 'report.html.tmp', 'diagnostics.log',
                    'results.sqlite3', 'results.sqlite3-wal', 'results.sqlite3-shm']


def read_manifest(directory):
    path = directory / MANIFEST
    if path.is_symlink():
        raise ValueError('拒绝符号链接输出清单: ' + str(path))
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('owner') != 'AiValgrind' or data.get('version') != 1:
        raise ValueError('输出清单格式无效，未清理: ' + str(path))
    for field in ('files', 'sessions'):
        names = data.get(field, [])
        if not isinstance(names, list) or any(not isinstance(n, str) or not n or n in ('.', '..') or '/' in n or '\\' in n or ':' in n for n in names):
            raise ValueError('输出清单路径无效，未清理: ' + str(path))
    return data


def write_manifest(directory, files, sessions=()):
    path = directory / MANIFEST
    if path.is_symlink():
        raise ValueError('拒绝符号链接输出清单: ' + str(path))
    path.write_text(json.dumps({'owner': 'AiValgrind', 'version': 1,
                               'files': list(files), 'sessions': list(sessions)}, indent=2), encoding='utf-8')


def prepare_output(directory, names, protected=()):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('结果目录不能是符号链接')
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    protected = {Path(p).resolve() for p in protected if p is not None}
    previous = read_manifest(directory)
    owned = previous.get('files', []) if previous else []
    if any(n not in COLLECTION_FILES for n in owned):
        raise ValueError('输出清单含未知文件，未清理')
    deletions, empty_dirs = [], []
    for name in sorted(set(owned) | set(names)):
        path = directory / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('输出位置包含链接或目录，未清理: ' + str(path))
        if path.exists() and name in names and path.resolve() in protected:
            raise ValueError('输出路径与本次输入文件重名，未删除任何文件: ' + str(path)
                             + '\n请使用其他 --output-dir，避免覆盖程序或输入数据。')
        if path.exists() and name in names and name not in owned:
            reason = '目录中没有 .aivalgrind-files.json 归属清单' if previous is None else '归属清单没有登记这个文件'
            raise ValueError('已有文件无法确认归属，未删除任何文件: ' + str(path)
                             + '\n原因: ' + reason + '。旧版本产生的文件也可能出现此情况。'
                             + '\n处理方法: 改用新的 --output-dir；或确认旧结果已备份后，自行整理同名文件。'
                             + '\n不要直接删除未知文件，也不要把整个结果目录清空。')
        if name in owned and path.exists() and path.resolve() not in protected:
            deletions.append(path)
    captures = directory / 'captures'
    for name in previous.get('sessions', []) if previous else []:
        session = captures / name
        if captures.is_symlink() or session.is_symlink():
            raise ValueError('采集目录包含链接，未清理: ' + str(session))
        data = read_manifest(session) if session.exists() else None
        if data:
            for filename in data['files']:
                if filename not in ('capture.py', 'valgrind.log', 'connection-error.txt') and not re.fullmatch(r'error-\d+\.(json|txt|html)', filename):
                    raise ValueError('采集清单含未知文件，未清理')
                path = session / filename
                if path.is_symlink() or (path.exists() and not path.is_file()):
                    raise ValueError('采集文件包含链接或目录，未清理: ' + str(path))
                if path.exists() and path.resolve() not in protected:
                    deletions.append(path)
            deletions.append(session / MANIFEST)
            empty_dirs.append(session)
    # All paths and collisions have been checked before the first deletion.
    for path in deletions:
        try:
            path.resolve().relative_to(directory)
        except ValueError:
            raise ValueError('输出路径越过结果目录，未清理: ' + str(path))
    for path in deletions:
        path.unlink()
    for path in empty_dirs:
        try:
            path.rmdir()  # Only succeeds when no unrelated file remains.
        except OSError:
            pass
    keep = [n for n in owned if (directory / n).resolve() in protected]
    write_manifest(directory, sorted(set(names) | set(keep)))
    return directory


def register_capture(session):
    write_manifest(session, ['capture.py', 'valgrind.log', 'connection-error.txt'])
    root = session.parent.parent
    if session.parent.name == 'captures' and not session.parent.is_symlink():
        data = read_manifest(root)
        if data:
            write_manifest(root, data['files'], data.get('sessions', []) + [session.name])
