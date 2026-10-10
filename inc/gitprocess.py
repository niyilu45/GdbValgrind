"""Interruptible Git queries without pipe-EOF waits on helper processes."""
import os
import signal
import subprocess
import tempfile
import time


def run_git(command, timeout, checkpoint=None):
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    # Helpers inheriting stdout cannot keep communicate() waiting for EOF.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=errors, start_new_session=(os.name == 'posix'),
                                 env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        try:
            deadline = time.monotonic() + timeout
            while child.poll() is None:
                checkpoint()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    child.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    pass
            checkpoint()
            output.seek(0)
            errors.seek(0)
            return subprocess.CompletedProcess(command, child.returncode,
                output.read().decode('utf-8', 'replace'), errors.read().decode('utf-8', 'replace'))
        finally:
            if os.name == 'posix':
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif child.poll() is None:
                child.kill()
            child.wait(timeout=2)
