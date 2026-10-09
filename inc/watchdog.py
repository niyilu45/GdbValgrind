"""Record a stalled collection's Python stacks once, without blocking collection."""
import sys
import threading
import time
import traceback


class CollectionWatchdog:
    def __init__(self, path, timeout=15):
        self.path, self.timeout = path, timeout
        self.progress = (time.monotonic(), '准备启动')
        self.stop = threading.Event()
        self.thread = None

    def mark(self, stage):
        self.progress = (time.monotonic(), stage)

    def __enter__(self):
        def monitor():
            recorded = None
            while not self.stop.wait(0.5):
                progress = self.progress
                if time.monotonic() - progress[0] < self.timeout or recorded == progress:
                    continue
                recorded = progress
                lines = ['Collection stage stalled: ' + progress[1], 'This is diagnostic evidence, not proof of a deadlock.']
                names = {thread.ident: thread.name for thread in threading.enumerate()}
                for ident, frame in sys._current_frames().items():
                    lines.append('\nThread ' + names.get(ident, str(ident)))
                    lines.extend(traceback.format_stack(frame))
                try:
                    self.path.write_text('\n'.join(lines), encoding='utf-8')
                except OSError:
                    pass
        self.thread = threading.Thread(target=monitor, name='aivalgrind-watchdog', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=2)
