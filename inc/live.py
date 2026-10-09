"""Loopback-only, read-only live report snapshots; no target/debug execution."""
import threading
import signal

from .core import DebugServer, Handler, render_html
from .processes import interrupt_scope


class LiveHandler(Handler):
    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'message': 'Host 不允许'})
        with self.server.lock:
            report, revision = self.server.report, self.server.revision
        if self.path == '/':
            return self.reply(200, render_html(report), 'text/html; charset=utf-8')
        if self.path.startswith('/api/live?revision='):
            previous = self.path.split('=', 1)[1]
            payload = {'revision': revision}
            if previous != str(revision):
                payload['report'] = report
            return self.reply(200, payload)
        return self.reply(404, {'message': '未找到'})


class LiveReport:
    def __init__(self, report, port):
        self.server = DebugServer({**report, 'live': True, 'collection_state': 'running'}, port)
        self.server.RequestHandlerClass = LiveHandler
        self.server.revision = 0
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)

    def __enter__(self):
        self.thread.start()
        port = self.server.server_port
        print('实时报告: ' + self.server.origin + '/', flush=True)
        print('SSH 用户请在本机建立转发（替换登录地址），然后打开上述链接：\n'
              '  ssh -N -L {0}:127.0.0.1:{0} 用户名@服务器'.format(port), flush=True)
        print('网页约每 2 秒更新；停止采集后服务关闭，结果保存在采集目录 report.html。', flush=True)
        return self

    def update(self, report, state):
        with self.server.lock:
            self.server.report = {**report, 'live': True, 'collection_state': state}
            self.server.revision += 1

    def __exit__(self, *exc):
        with interrupt_scope(signal.SIG_IGN):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
