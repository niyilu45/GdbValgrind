"""Incremental, disk-backed collection. One XML error is normalized only once."""
import json
import sqlite3
from pathlib import Path
import xml.etree.ElementTree as ET
from contextlib import contextmanager

from .core import report_from_root, integer


class ErrorStore:
    def __init__(self, path, xml_path):
        self.path, self.xml_path = Path(path), xml_path
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.executescript('''
            CREATE TABLE issues(seq INTEGER PRIMARY KEY, id TEXT UNIQUE, kind TEXT,
                what TEXT, payload TEXT, count INTEGER, records INTEGER,
                bytes INTEGER, blocks INTEGER, files TEXT, search TEXT,
                author TEXT DEFAULT '', enriched TEXT, retry_at REAL DEFAULT 0,
                author_done INTEGER DEFAULT 0, ready INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
                detail_version INTEGER DEFAULT 0);
            CREATE TRIGGER detail_changed AFTER UPDATE OF enriched ON issues BEGIN
                UPDATE issues SET detail_version=detail_version+1 WHERE seq=new.seq;
            END;
            CREATE TABLE uids(uid TEXT PRIMARY KEY, issue TEXT, count INTEGER, known INTEGER);
            CREATE INDEX kinds ON issues(kind);
            CREATE INDEX authors ON issues(author);
            CREATE INDEX enrichment_state ON issues(author_done,seq);
            CREATE INDEX source_pending ON issues(author_done,retry_at,seq) WHERE author_done<2 AND attempts<3;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE rollups(kind TEXT,author TEXT,pending INTEGER,n INTEGER,
                PRIMARY KEY(kind,author,pending));
            CREATE TRIGGER issue_added AFTER INSERT ON issues BEGIN
                INSERT OR IGNORE INTO rollups VALUES(new.kind,new.author,new.author_done=0,0);
                UPDATE rollups SET n=n+1 WHERE kind=new.kind AND author=new.author AND pending=(new.author_done=0);
            END;
            CREATE TRIGGER issue_group_changed AFTER UPDATE OF kind,author,author_done ON issues
            WHEN old.kind!=new.kind OR old.author!=new.author OR (old.author_done=0)!=(new.author_done=0)
            BEGIN
                UPDATE rollups SET n=n-1 WHERE kind=old.kind AND author=old.author AND pending=(old.author_done=0);
                DELETE FROM rollups WHERE n=0;
                INSERT OR IGNORE INTO rollups VALUES(new.kind,new.author,new.author_done=0,0);
                UPDATE rollups SET n=n+1 WHERE kind=new.kind AND author=new.author AND pending=(new.author_done=0);
            END;
        ''')
        self.kinds = {}
        self.revision = 0
        self.unknown = 0
        self.finished = False

    def accept(self, node):
        if node.tag == 'pair':
            uid = node.findtext('unique', '')
            count = max(1, integer(node.findtext('count'), 1))
            old = self.db.execute('SELECT issue,count,known FROM uids WHERE uid=?', (uid,)).fetchone()
            if old:
                issue, previous, known = old
                count = max(count, previous)
                self.db.execute('UPDATE uids SET count=?,known=1 WHERE uid=?', (count, uid))
                if issue:
                    if not known:
                        self.unknown -= 1
                    kind = self.db.execute('SELECT kind FROM issues WHERE id=?', (issue,)).fetchone()[0]
                    self.db.execute('UPDATE issues SET count=count+? WHERE id=?', (count-previous, issue))
                    self.kinds[kind]['occurrences'] += count-previous
            else:
                self.db.execute('INSERT INTO uids VALUES (?,NULL,?,1)', (uid, count))
            self.revision += 1
            return True
        if node.tag == 'error':
            uid = node.findtext('unique', '')
            old = self.db.execute('SELECT issue,count,known FROM uids WHERE uid=?', (uid,)).fetchone() if uid else None
            if old and old[0]:
                return True
            root = ET.Element('valgrindoutput')
            root.append(node)
            item = report_from_root(root, self.xml_path, xml_complete=False)['errors'][0]
            count, known = (old[1], old[2]) if old else (1, 0)
            frames = [f for s in item['stacks'] for f in s['frames']]
            files = '\n'.join((f.get('dir', '') + '/' + f.get('file', '')).replace('\\', '/') for f in frames).lower()
            search = '\n'.join([item['kind'], item['what'], item['id'], files] + [f.get('fn', '')+' '+f.get('obj', '') for f in frames]).lower()
            payload = json.dumps({k:v for k,v in item.items() if k != 'unique_ids'}, ensure_ascii=False)
            cursor = self.db.execute('INSERT OR IGNORE INTO issues(id,kind,what,payload,count,records,bytes,blocks,files,search) VALUES (?,?,?,?,0,0,0,0,?,?)',
                                    (item['id'], item['kind'], item['what'], payload, files, search))
            totals = self.kinds.setdefault(item['kind'], {'locations': 0, 'occurrences': 0})
            totals['locations'] += cursor.rowcount
            totals['occurrences'] += count
            self.db.execute('UPDATE issues SET count=count+?,records=records+1,bytes=bytes+?,blocks=blocks+? WHERE id=?',
                            (count, item['leaked_bytes'], item['leaked_blocks'], item['id']))
            if uid:
                self.db.execute('INSERT OR REPLACE INTO uids VALUES (?,?,?,?)', (uid,item['id'],count,known))
            self.unknown += not known
            self.revision += 1
            return True
        if node.tag == 'status':
            self.finished = node.findtext('state') == 'FINISHED'
        return False

    def summary(self, complete=False):
        return {'kinds': {k:dict(v) for k,v in self.kinds.items()}, 'locations': sum(k['locations'] for k in self.kinds.values()),
                'occurrences': sum(k['occurrences'] for k in self.kinds.values()),
                'finished': complete and self.finished,
                'counts_complete': complete and self.finished and not self.unknown}

    def commit(self, status=None):
        if status is not None:
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('status',json.dumps(status)))
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()


@contextmanager
def connect(path):
    db = sqlite3.connect(str(path), timeout=2)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def issue_data(row):
    item = json.loads(row['enriched'] or row['payload'])
    item.update(count=row['count'], records=row['records'], leaked_bytes=row['bytes'], leaked_blocks=row['blocks'])
    return item


def load_replay(path, xml_path, progress=None, *, include_enriched=False):
    """Reuse the just-collected database without reading XML or source snippets."""
    from .commands import command_metadata
    root=ET.Element('valgrindoutput')
    report=report_from_root(root,xml_path,xml_complete=False)
    with connect(path) as db:
        status_row=db.execute("SELECT value FROM metadata WHERE key='status'").fetchone()
        status=json.loads(status_row[0]) if status_row else {}
        total=status.get('locations',0)
        cursor=db.execute('SELECT payload,'+('enriched' if include_enriched else 'NULL AS enriched')+
                          ',count,records,bytes,blocks FROM issues ORDER BY seq')
        for row in cursor:
            report['errors'].append(issue_data(row))
            if progress and len(report['errors'])%100==0:
                progress('读取 SQLite 复现位置 | %d / %d 条' % (len(report['errors']),total))
    report.update(occurrences=status.get('occurrences',0),finished=status.get('finished',False),
                  xml_complete=status.get('finished',False),counts_complete=status.get('counts_complete',False))
    report['debug_command']=command_metadata(root,xml_path)
    return report
