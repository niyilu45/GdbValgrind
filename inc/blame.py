"""Best-effort, bounded Git attribution for displayed source excerpts."""
from datetime import datetime, timezone
import re
import shutil
import subprocess
import time

_cache = {}


def source_blame(path, start, end, timeout=5):
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        now = time.monotonic()
        cached = _cache.get(key)
        if cached and now - cached[0] < 60 and all(n in cached[1] for n in range(start, end + 1)):
            return {n: row for n, row in cached[1].items() if start <= n <= end}
        git = shutil.which('git')
        if not git:
            return {}
        result = subprocess.run([git, '--no-pager', '-C', str(path.parent),
                                 'blame', '--line-porcelain', '-L', '%d,%d' % (start, end), '--', path.name],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, text=True, encoding='utf-8', errors='replace')
        rows, current, number = {}, {}, None
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                header = re.fullmatch(r'([0-9a-f]{40,64}) (\d+) (\d+)(?: \d+)?', line)
                if header:
                    number = int(header[3])
                    current = {'commit': header[1]}
                elif line.startswith('\t') and number is not None:
                    if current.get('commit', '').strip('0') == '':
                        current = {'commit': '', 'author': '未提交修改', 'date': '', 'summary': ''}
                    rows[number] = {**current, 'text': line[1:]}
                    number = None
                elif line.startswith('author '):
                    current['author'] = line[7:]
                elif line.startswith('author-time '):
                    current['date'] = datetime.fromtimestamp(int(line[12:]), timezone.utc).strftime('%Y-%m-%d')
                elif line.startswith('summary '):
                    current['summary'] = line[8:]
        if rows:
            if cached and now - cached[0] < 60:
                rows = {**cached[1], **rows}
            if len(_cache) >= 128:
                del _cache[next(iter(_cache))]
            # Keep the original expiry when extending a cached file, so old
            # lines do not remain stale indefinitely under frequent queries.
            _cache[key] = (cached[0] if cached and now - cached[0] < 60 else time.monotonic(), rows)
        return {n: row for n, row in rows.items() if start <= n <= end}
    except (OSError, ValueError, OverflowError, subprocess.SubprocessError):
        return {}
