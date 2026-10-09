"""Bounded log tail and fixed terminal dashboard (standard library only)."""
import codecs
from collections import deque
from datetime import datetime
import os
import shutil
import sys
import time
import unicodedata
import select


def clip(text, width):
    result, used = [], 0
    for char in str(text):
        if unicodedata.category(char).startswith('C'):
            char = ' '  # Never let target output issue terminal control commands.
        size = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1)
        if used + size > width:
            break
        result.append(char)
        used += size
    return ''.join(result)


class TerminalProgress:
    def __init__(self, directory, started_at=None, started_clock=None):
        self.directory = directory
        self.started_at = started_at or datetime.now().astimezone().isoformat(timespec='seconds')
        self.clock = time.monotonic() if started_clock is None else started_clock
        self.screen = sys.stdout
        self.active = False
        self.tty = self.screen.isatty() and os.environ.get('TERM') != 'dumb'
        self.lines = deque(maxlen=300)
        self.streams = []
        self.keyboard = None
        self.terminal_settings = None

    def elapsed(self):
        return max(0, time.monotonic() - self.clock)

    def __enter__(self):
        try:
            for name, label in [('program.log', 'stdout'), ('launcher.log', 'stderr')]:
                self.streams.append([open(self.directory / name, 'rb'), label,
                                     codecs.getincrementaldecoder('utf-8')(errors='replace'), ''])
            if sys.platform == 'linux' and sys.stdin.isatty():
                import termios
                fd = sys.stdin.fileno()
                settings = termios.tcgetattr(fd)
                updated = settings[:]
                updated[6] = settings[6][:]
                updated[3] = (updated[3] & ~(termios.ICANON | termios.ECHO)) | termios.ISIG
                updated[0] &= ~termios.IXON
                updated[6][termios.VINTR] = b'\x03'
                updated[6][termios.VMIN] = 1
                updated[6][termios.VTIME] = 0
                self.keyboard, self.terminal_settings = fd, (fd, settings)
                termios.tcsetattr(fd, termios.TCSANOW, updated)
            if self.tty:
                self.active = True
                self.screen.write('\x1b[?1049h\x1b[?25l')
                self.screen.flush()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def check_quit(self):
        if self.keyboard is not None and select.select([self.keyboard], [], [], 0)[0]:
            keys = os.read(self.keyboard, 1024)
            if not keys:
                self.keyboard = None
            elif b'q' in keys.lower() or b'\x03' in keys:
                raise KeyboardInterrupt()

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            self.check_quit()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def read_logs(self):
        for entry in self.streams:
            file, label, decoder, pending = entry
            end = os.fstat(file.fileno()).st_size
            if end - file.tell() > 65536:
                file.seek(end - 65536)
                decoder.reset()
                pending = ''
                self.lines.append('[%s] 输出较多，仅显示最新部分；完整内容已写入日志。' % label)
            data = decoder.decode(file.read(65536))
            parts = (pending + data.replace('\r', '\n')).split('\n')
            for line in parts[:-1]:
                self.lines.append('[' + label + '] ' + line[:2000])
            entry[3] = parts[-1][-2000:]

    def render(self, summary, state, url=''):
        self.read_logs()
        if not self.active:
            return
        width, height = shutil.get_terminal_size((100, 28))
        elapsed = int(self.elapsed())
        duration = '%02d:%02d:%02d' % (elapsed // 3600, elapsed // 60 % 60, elapsed % 60)
        prefix = '' if summary['counts_complete'] else '至少 '
        rows = ['AiValgrind 内存检测 | ' + {'running': '运行中', 'finished': '已结束', 'failed': '失败', 'interrupted': '已中断'}.get(state, state),
                '开始: ' + self.started_at + ' | 已运行: ' + duration,
                '错误: %d 种 / %d 个位置 / %s%d 次' % (len(summary['kinds']), summary['locations'], prefix, summary['occurrences']),
                '类型: ' + ('; '.join('%s=%d' % (k, v['locations']) for k, v in sorted(summary['kinds'].items())) or '暂未发现'),
                '实时网页: ' + (url or '未启用'),
                '日志目录: ' + str(self.directory),
                '程序输出（最新内容；可能受程序缓冲影响） | q / Ctrl+C 退出（无需回车）']
        tail = list(self.lines) + ['[' + item[1] + '] ' + item[3] for item in self.streams if item[3]]
        available = max(0, height - 1 - len(rows))
        rows += tail[-available:] if available else []
        rows = rows[:max(1, height - 1)]
        # Keep everything within the viewport; never append to terminal scrollback.
        self.screen.write('\x1b[H' + '\r\n'.join(clip(row, max(1, width - 1)) + '\x1b[K' for row in rows) + '\x1b[J')
        self.screen.flush()

    def __exit__(self, *exc):
        try:
            if self.active:
                self.screen.write('\x1b[?25h\x1b[?1049l')
                self.screen.flush()
        finally:
            self.active = False
            if self.terminal_settings is not None:
                import termios
                fd, settings = self.terminal_settings
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, settings)
                except (OSError, termios.error):
                    pass  # SSH terminal may already be gone; still close our files.
                finally:
                    self.terminal_settings = None
                    self.keyboard = None
            for entry in self.streams:
                entry[0].close()
