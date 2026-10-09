"""PTY output transport: let programs line-buffer stdout, preserve separate logs."""
import errno
import os
import select
import threading


class ProgramOutput:
    def __init__(self, stdout, stderr):
        self.logs = [stdout, stderr]
        self.pairs = []
        self.stop = threading.Event()
        self.thread = None
        self.error = None

    def __enter__(self):
        import pty
        import termios
        try:
            for _ in self.logs:
                master, slave = pty.openpty()
                self.pairs.append((master, slave))
                settings = termios.tcgetattr(slave)
                settings[1] &= ~termios.OPOST
                termios.tcsetattr(slave, termios.TCSANOW, settings)
                os.set_blocking(master, False)
            self.thread = threading.Thread(target=self.pump, name='aivalgrind-output', daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.close()
            raise

    @property
    def stdout(self): return self.pairs[0][1]

    @property
    def stderr(self): return self.pairs[1][1]

    def pump(self):
        try:
            masters = {pair[0]: log for pair, log in zip(self.pairs, self.logs)}
            while masters:
                ready = select.select(list(masters), [], [], 0.1)[0]
                if not ready and self.stop.is_set():
                    break
                for fd in ready:
                    try:
                        data = os.read(fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        data = b''
                    if not data:
                        del masters[fd]
                    else:
                        masters[fd].write(data)
                        masters[fd].flush()
        except Exception as exc:
            self.error = exc

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise OSError('程序输出线程未能及时退出')
        for pair in self.pairs:
            for fd in pair:
                os.close(fd)
        self.pairs = []
        self.thread = None
        if self.error:
            raise OSError('程序输出读取失败: ' + str(self.error))

    def __exit__(self, *exc):
        self.close()
