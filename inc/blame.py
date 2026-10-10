"""Best-effort, bounded Git attribution for displayed source excerpts."""
from datetime import datetime, timezone
import re
import shutil
import subprocess
import time

_cache = {}


class BlameRows(dict):
    def __init__(self, note):
        super().__init__()
        self.note = note


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
            return BlameRows('未安装 Git 或 PATH 中找不到 Git')
        result = subprocess.run([git, '--no-pager', '-C', str(path.parent),
                                 'blame', '--line-porcelain', '-L', '%d,%d' % (start, end), '--', path.name],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, text=True, encoding='utf-8', errors='replace')
        rows, current, number = {}, {}, None
        if result.returncode != 0:
            reason = getattr(result, 'stderr', '').strip().replace('\n', ' ')[:250]
            return BlameRows('Git 查询失败（退出码 %s）：%s' % (result.returncode, reason or '文件可能未跟踪或不在 Git 工程内'))
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
        return {n: row for n, row in rows.items() if start <= n <= end} if rows else BlameRows('Git 未返回可解析的行归属')
    except subprocess.TimeoutExpired:
        return BlameRows('Git 查询超过 %.1f 秒，已跳过' % timeout)
    except (OSError, ValueError, OverflowError, subprocess.SubprocessError):
        return BlameRows('Git 查询或解析失败，已跳过')
