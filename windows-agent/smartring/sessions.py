"""Bounded, read-only Codex history. Never resume threads or write the source DB."""
import hashlib,json,sqlite3,threading,time,uuid
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from .core import GuardError

def clip_utf8(text,limit):
    return str(text).encode('utf8')[:limit].decode('utf8','ignore')

def canonical_path(value):
    path=str(Path(value).resolve())
    if path.startswith('\\\\?\\UNC\\'):path='\\\\'+path[8:]
    elif path.startswith('\\\\?\\'):path=path[4:]
    return Path(path)

class CodexHistory:
    def __init__(self,home=None):
        self.home=canonical_path(home or Path.home()/'.codex')
        self.database=self.home/'state_5.sqlite'
        self.seen={};self.cache={};self.lock=threading.RLock()
        self.notification=None;self.poll_error='';self.ready=False
    @contextmanager
    def connection(self):
        if not self.database.is_file():raise GuardError('CODEX_DATABASE_NOT_FOUND')
        c=sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True,timeout=.15)
        c.execute('PRAGMA query_only=ON');c.row_factory=sqlite3.Row
        try:yield c
        finally:c.close()
    def rows(self,offset=0,limit=3):
        offset=max(0,min(int(offset),10000));limit=max(1,min(int(limit),16))
        with self.connection() as c:
            cols={x[1] for x in c.execute('PRAGMA table_info(threads)')}
            if not {'id','title','rollout_path','updated_at','archived','source'}<=cols:raise GuardError('CODEX_SCHEMA_UNSUPPORTED')
            where="archived=0 AND source NOT LIKE '{%'"
            if 'thread_source' in cols:where+=" AND (thread_source IN ('user','agent_created_thread') OR thread_source IS NULL)"
            total=c.execute('SELECT count(*) FROM threads WHERE '+where).fetchone()[0]
            title="COALESCE(NULLIF(name,''),title) AS title" if 'name' in cols else 'title'
            rows=c.execute('SELECT id,'+title+',updated_at,rollout_path FROM threads WHERE '+where+' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?',(limit,offset)).fetchall()
        return [dict(row) for row in rows],total
    def get(self,identifier):
        try:uuid.UUID(identifier)
        except (ValueError,TypeError,AttributeError):raise GuardError('BAD_SESSION_ID')
        with self.connection() as c:
            cols={x[1] for x in c.execute('PRAGMA table_info(threads)')}
            title="COALESCE(NULLIF(name,''),title) AS title" if 'name' in cols else 'title'
            row=c.execute('SELECT id,'+title+',updated_at,rollout_path FROM threads WHERE id=? AND archived=0 AND source NOT LIKE \'{%\'',(identifier,)).fetchone()
        if row is None:raise GuardError('SESSION_NOT_FOUND')
        result=dict(row);path=canonical_path(result['rollout_path'])
        if not any(path.is_relative_to(self.home/folder) for folder in ('sessions','archived_sessions')):raise GuardError('SESSION_PATH_REJECTED')
        result['path']=path;return result
    def latest(self,identifier):
        row=self.get(identifier);path=row['path']
        if not path.is_file():raise GuardError('SESSION_LOG_NOT_FOUND')
        info=path.stat();signature=(info.st_size,info.st_mtime_ns)
        with self.lock:
            cached=self.cache.get(identifier)
            if cached and cached[0]==signature:return dict(cached[1])
        # Reverse scan at most 2 MiB. A huge/truncated line is never parsed wholesale.
        with path.open('rb') as f:
            start=max(0,info.st_size-2*1024*1024);f.seek(start);lines=f.read(2*1024*1024).splitlines()
        if start and lines:lines=lines[1:]
        text='';phase='';timestamp=''
        for raw in reversed(lines):
            if len(raw)>1024*1024:continue
            try:item=json.loads(raw)
            except (ValueError,UnicodeError):continue
            p=item.get('payload',{})
            if item.get('type')=='response_item' and p.get('type')=='message' and p.get('role')=='assistant':
                parts=[x.get('text','') for x in p.get('content',[]) if x.get('type') in ('output_text','text')]
                if parts:
                    text='\n'.join(parts);phase=p.get('phase') or p.get('channel') or 'assistant';timestamp=item.get('timestamp','');break
            if item.get('type')=='event_msg' and p.get('type')=='agent_message' and p.get('message'):
                text=p['message'];phase=p.get('phase') or 'assistant';timestamp=item.get('timestamp','');break
        if not text:raise GuardError('NO_ASSISTANT_REPLY_IN_BOUNDED_TAIL')
        digest=hashlib.sha256(text.encode('utf8')).hexdigest()
        result=dict(id=identifier,title=clip_utf8(row['title'],162),text=text[:24000],truncated=len(text)>24000,
                    digest=digest,phase=phase,timestamp=timestamp,updated=row['updated_at'])
        with self.lock:
            if len(self.cache)>=32:self.cache.pop(next(iter(self.cache)))
            self.cache[identifier]=(signature,result)
        return dict(result)
    def list_page(self,offset=0):
        rows,total=self.rows(offset,3);items=[]
        for row in rows:
            unread=False
            with self.lock:
                cached=self.cache.get(row['id'])
                if cached:unread=self.seen.get(row['id']) not in (None,cached[1]['digest'])
            items.append(dict(id=row['id'],title=clip_utf8(row['title'],162),updated=datetime.fromtimestamp(row['updated_at']).strftime('%m-%d %H:%M'),
                readable=Path(row['rollout_path']).is_file(),verification='输入目标待验证',unread=unread))
        return dict(items=items,offset=int(offset),has_next=int(offset)+len(items)<total,total=total,source='readonly_codex_history')
    def reply_page(self,identifier,offset=0):
        result=self.latest(identifier);offset=max(0,int(offset))
        data=result['text'].encode('utf8');offset=min(offset,len(data))
        # Reject byte cursors in the middle of a codepoint.
        if offset<len(data) and data[offset]&0xC0==0x80:raise GuardError('BAD_REPLY_CURSOR')
        piece=data[offset:offset+1200].decode('utf8','ignore');next_offset=offset+len(piece.encode('utf8'))
        with self.lock:self.seen[identifier]=result['digest']
        return dict(id=identifier,title=result['title'],body=piece,offset=offset,next_offset=next_offset,
                    has_next=next_offset<len(data),truncated=result['truncated'],digest=result['digest'],phase=result['phase'],verified=False)
    def poll(self,busy=lambda:False):
        try:
            rows,_=self.rows(0,16)
            for row in reversed(rows):
                if busy():return
                try:reply=self.latest(row['id'])
                except (GuardError,OSError,sqlite3.Error):continue
                with self.lock:
                    previous=self.seen.get(row['id'])
                    if previous is None:self.seen[row['id']]=reply['digest']
                    elif previous!=reply['digest']:
                        self.notification=dict(app='Codex',id=row['id'],title=reply['title'],digest=reply['digest'],phase=reply['phase'])
            self.ready=True;self.poll_error=''
        except (OSError,sqlite3.Error,GuardError) as e:self.poll_error=str(e)[:100]
    def start(self,busy=lambda:False):
        def worker():
            while True:
                if not busy():self.poll(busy)
                time.sleep(5)
        threading.Thread(target=worker,daemon=True,name='codex-readonly-history').start()
    def dismiss(self):
        with self.lock:
            if self.notification:self.seen[self.notification['id']]=self.notification['digest']
            self.notification=None
