"""SQLite paging and one cancellable source worker, isolated from collection."""
import json
import queue
import signal
import threading
import time
import sqlite3
from urllib.parse import urlsplit, parse_qs

from .core import DebugServer, Handler, Sources, render_html, integer
from .processes import interrupt_scope
from .store import connect, issue_data
from .sourcepages import browser_report


class SourceCancelled(BaseException):
    pass


class PagedHandler(Handler):
    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'message': 'Host 不允许'})
        owner = self.server.owner
        path = urlsplit(self.path)
        if path.path == '/':
            return self.reply(200, render_html(owner.report), 'text/html; charset=utf-8')
        if path.path.startswith('/source/'):
            with owner.files_lock:
                report = {**owner.report, 'source_files': dict(owner.source_files)}
            return self.reply_source(report)
        try:
            args = parse_qs(path.query)
            value = lambda name: args.get(name, [''])[0][:1000]
            with connect(owner.path) as db:
                if path.path == '/api/issues':
                    offset = max(0, int(value('offset') or 0))
                    clauses, params = [], []
                    for column, query in [('search', value('q')), ('files',value('file'))]:
                        if query:
                            clauses.append('instr('+column+',?)>0')
                            params.append(query.replace('\\','/').lower())
                    author_where = ' AND '.join(clauses) or '1'
                    author_params = list(params)
                    if value('author'):
                        clauses.append('author=?')
                        params.append('' if value('author') == 'missing' else value('author')[7:])
                    base = ' AND '.join(clauses) or '1'
                    groups = list(db.execute('SELECT kind,author,pending,n FROM rollups'))
                    totals, kinds, author_counts, pending = {}, {}, {}, 0
                    for group in groups:
                        k, author, n = group['kind'], group['author'], group['n']
                        totals[k] = totals.get(k,0)+n
                        author_counts[author] = author_counts.get(author,0)+(0 if k=='Leak_StillReachable' else n)
                        pending += group['pending']*n
                        if not value('author') or author == ('' if value('author')=='missing' else value('author')[7:]):
                            kinds[k] = kinds.get(k,0)+n
                    if value('q') or value('file'):
                        kinds = {r['kind']:r['n'] for r in db.execute('SELECT kind,count(*) n FROM issues WHERE '+base+' GROUP BY kind',params)}
                        author_counts = {r['author']:r['n'] for r in db.execute(
                            "SELECT author,sum(CASE WHEN kind='Leak_StillReachable' THEN 0 ELSE 1 END) n FROM issues WHERE "
                            +author_where+' GROUP BY author', author_params)}
                    if value('kind'):
                        clauses.append('kind=?'); params.append(value('kind'))
                    where = ' AND '.join(clauses) or '1'
                    total = kinds.get(value('kind'),0) if value('kind') else sum(kinds.values())
                    rows = [dict(r) for r in db.execute('SELECT id,kind,what,count,seq FROM issues WHERE '+where+' ORDER BY seq LIMIT 100 OFFSET ?', params+[offset])]
                    authors = [{'author':a,'n':n} for a,n in sorted(author_counts.items(),key=lambda p:(-p[1],p[0]))]
                    status = db.execute("SELECT value FROM metadata WHERE key='status'").fetchone()
                    return self.reply(200, {'errors': rows, 'total':total,'kinds':kinds,'totals':totals,'authors':authors,'pending':pending,'status':json.loads(status[0]) if status else {},'worker_error':owner.report.get('source_worker_error','')})
                if path.path == '/api/issue':
                    row = db.execute('SELECT * FROM issues WHERE id=?',(value('id'),)).fetchone()
                    if row is None:
                        return self.reply(404, {'message':'错误不存在'})
                    if not row['ready']:
                        owner.request(row['id'])
                    item = issue_data(row)
                    # Raw database entries are parsed without source enrichment.
                    # That must not be presented as a missing user argument.
                    if owner.report.get('project'):
                        for stack in item['stacks']:
                            for frame in stack['frames']:
                                if not frame.get('source') and frame.get('source_note') == '未指定工程目录':
                                    frame['source_note'] = '源码尚在后台加载' if not row['ready'] else '该栈帧未找到可用源码'
                    with owner.files_lock:
                        # Only file IDs referenced by this detail need to travel.
                        ids = {f.get('source_file_id') for s in item['stacks'] for f in s['frames']}
                        files = {k:v for k,v in owner.source_files.items() if k in ids}
                    payload = browser_report({'errors':[item], 'source_files':files,
                                              'project':owner.report.get('project',''), 'source_pending':not bool(row['ready'])},compact=True)
                    return self.reply(200, {'report':payload,'ready':bool(row['ready'])})
        except (ValueError, OSError) as exc:
            return self.reply(400, {'message': str(exc)})
        except sqlite3.Error:
            return self.reply(503, {'message': '数据库忙，请稍后重试'})
        return self.reply(404, {'message':'未找到'})


class PagedReport:
    def __init__(self, path, report, port):
        self.path, self.report = path, {**report, 'paged':True, 'live':False}
        self.server = DebugServer(self.report, port)
        self.server.RequestHandlerClass = PagedHandler
        self.server.owner = self
        self.stop = threading.Event()
        self.requests = queue.Queue(maxsize=100)
        self.pending = set()
        self.pending_lock = threading.Lock()
        self.files_lock = threading.Lock()
        self.source_files = {}
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.1},daemon=True)
        self.worker = threading.Thread(target=self.enrich, name='aivalgrind-source',daemon=True)

    def request(self, identity):
        with self.pending_lock:
            if identity not in self.pending and not self.requests.full():
                self.pending.add(identity); self.requests.put_nowait(identity)

    def checkpoint(self, text=''):
        if self.stop.is_set():
            raise SourceCancelled()

    def enrich(self):
        while not self.stop.is_set():
            try:
                self._enrich_pass()
                return
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                    self.report['source_worker_error'] = str(exc)
                    return
                self.stop.wait(0.2)

    def _enrich_pass(self):
        identity = None
        try:
            sources = Sources(self.report.get('project'), progress=self.checkpoint)
            with connect(self.path) as db:
                while not self.stop.is_set():
                    if not self.report.get('project'):
                        # Without a project there is no source work to perform.
                        db.execute('UPDATE issues SET author_done=2,ready=1 WHERE author_done=0')
                        db.commit()
                        self.stop.wait(0.5)
                        continue
                    identity = None
                    try:
                        identity = self.requests.get_nowait()
                    except queue.Empty:
                        pass
                    if identity:
                        row = db.execute('SELECT * FROM issues WHERE id=?',(identity,)).fetchone()
                    else:
                        row = db.execute('SELECT * FROM issues WHERE author_done<2 AND attempts<3 AND retry_at<? ORDER BY seq LIMIT 1',(time.time(),)).fetchone()
                    if row is None:
                        self.stop.wait(0.2); continue
                    if identity and row['ready']:
                        with self.pending_lock:
                            self.pending.discard(identity)
                        continue
                    item = issue_data(row)
                    # Reuse the directory index across errors; bound full-file caches.
                    sources.files = {}
                    sources.blame_cache.clear()
                    if len(sources.cache) > 32:
                        sources.cache.clear()
                    if len(sources.resolved) > 8192:
                        sources.resolved.clear()
                    frames = [f for s in item['stacks'] for f in s['frames']] if identity else (item['stacks'][0]['frames'][:1] if item['stacks'] else [])
                    # Publish code before slow attribution queries finish.
                    for frame in frames:
                        self.checkpoint()
                        sources.enrich(frame, with_blame=False)
                    with self.files_lock:
                        self.source_files.update(sources.files)
                    item['source_progress'] = 'blame 已处理 0/%d 个栈帧；可继续浏览或切换错误' % len(frames)
                    db.execute('UPDATE issues SET enriched=? WHERE id=?', (json.dumps(item,ensure_ascii=False),row['id']))
                    db.commit()
                    for index, frame in enumerate(frames):
                        self.checkpoint()
                        sources.enrich(frame)
                        # Publish each completed frame, not only the whole stack.
                        # Git queries run outside the SQLite write transaction.
                        item['source_progress'] = 'blame 已处理 %d/%d 个栈帧；可继续浏览或切换错误' % (index + 1, len(frames))
                        db.execute('UPDATE issues SET enriched=? WHERE id=?',
                                   (json.dumps(item,ensure_ascii=False),row['id']))
                        db.commit()
                    first = item['stacks'][0]['frames'][0] if item['stacks'] and item['stacks'][0]['frames'] else {}
                    author = next((r.get('blame',{}).get('author','') for r in first.get('source',[]) if r['number']==integer(first.get('line'))), '')
                    with self.files_lock:
                        self.source_files.update(sources.files)
                    # Never overwrite counts accumulated by the collection writer.
                    db.execute('UPDATE issues SET enriched=?,author=?,author_done=?,ready=max(ready,?),attempts=attempts+1,retry_at=? WHERE id=?',
                               (json.dumps(item,ensure_ascii=False),author,2 if author or not first.get('source') else 1,int(bool(identity)),time.time()+15,row['id']))
                    db.commit()
                    if identity:
                        with self.pending_lock:
                            self.pending.discard(identity)
        except SourceCancelled:
            pass
        except sqlite3.OperationalError:
            if identity:
                with self.pending_lock:
                    self.pending.discard(identity)
                self.request(identity)
            raise
        except Exception as exc:
            self.report['source_worker_error'] = str(exc)

    def __enter__(self):
        self.thread.start(); self.worker.start()
        print('实时分页报告: '+self.server.origin+'/',flush=True)
        port=self.server.server_port
        print('SSH 转发: ssh -N -L {0}:127.0.0.1:{0} 用户名@服务器\n错误立即入库；源码和 blame 后台加载。停止后可从 errors.xml 导出完整报告。'.format(port),flush=True)
        return self

    def __exit__(self,*exc):
        self.stop.set()
        with interrupt_scope(signal.SIG_IGN):
            self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
            self.worker.join(timeout=3)
        if self.worker.is_alive():
            raise OSError('源码读取线程未能及时退出，请检查工程所在文件系统')
