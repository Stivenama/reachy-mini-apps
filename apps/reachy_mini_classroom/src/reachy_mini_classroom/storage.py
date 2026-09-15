"""Only text and timing metadata are persisted. Existing transcripts are preserved."""
import sqlite3
from contextlib import contextmanager
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "classes.sqlite3"
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'paused', summary TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS blocks(
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, number INTEGER NOT NULL,
                    closed INTEGER NOT NULL DEFAULT 0, UNIQUE(session, number));
                CREATE TABLE IF NOT EXISTS pieces(
                    id INTEGER PRIMARY KEY, block INTEGER NOT NULL, path TEXT NOT NULL,
                    seconds REAL NOT NULL DEFAULT 0, voiced INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'recording', text TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '');
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(blocks)')}
            if 'duration' not in columns:
                db.execute('ALTER TABLE blocks ADD COLUMN duration REAL')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def query(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).lastrowid

    def recover(self):
        self.execute("UPDATE pieces SET status='done',error='Texto parcial recuperado tras una interrupción' WHERE status='draft' AND text!=''")
        self.execute("UPDATE pieces SET status='done' WHERE status='draft' AND text=''")
        self.execute("UPDATE pieces SET status='failed',error='Fragmento anterior sin transcripción completa' WHERE status IN ('recording','processing','pending')")
        self.execute("UPDATE blocks SET closed=1 WHERE closed=0")
        self.execute("UPDATE sessions SET status='paused',error='Sesión interrumpida; se conserva el texto recibido.' WHERE status IN ('listening','finishing','responding','starting')")

    def new_session(self, title):
        sid = uuid.uuid4().hex
        self.execute("INSERT INTO sessions(id,title,created) VALUES(?,?,?)", (sid, title[:160] or 'Clase', time.time()))
        return sid

    def new_block(self, sid):
        return self.execute("INSERT INTO blocks(session,number) SELECT ?,COALESCE(MAX(number),0)+1 FROM blocks WHERE session=?", (sid, sid))

    def new_piece(self, block):
        return self.execute("INSERT INTO pieces(block,path,status) VALUES(?,'','draft')", (block,))

    def sessions(self):
        return self.query("SELECT * FROM sessions ORDER BY created DESC")

    def detail(self, sid):
        rows = self.query("SELECT * FROM sessions WHERE id=?", (sid,))
        if not rows:
            raise ValueError('Clase desconocida')
        result = rows[0]
        result['blocks'] = self.query("SELECT * FROM blocks WHERE session=? ORDER BY number", (sid,))
        for block in result['blocks']:
            pieces = self.query("SELECT id,seconds,status,text,error,attempts FROM pieces WHERE block=? ORDER BY id", (block['id'],))
            block['pieces'] = pieces
            block['seconds'] = block['duration'] if block['duration'] is not None else sum(p['seconds'] for p in pieces)
            block['text'] = '\n'.join(p['text'] for p in pieces if p['text'])
            block['ready'] = bool(block['closed']) and all(p['status'] == 'done' for p in pieces)
        return result

    def transcript(self, sid):
        d = self.detail(sid)
        parts = [d['title']]
        # The summary leads the file: on a three-hour class it is unreachable at the end.
        if d['summary'].strip():
            parts.append(f"Resumen de la clase\n{d['summary'].strip()}")
        parts += [f"Bloque {b['number']} ({b['seconds']/60:.1f} min)\n{b['text']}" for b in d['blocks']]
        return '\n\n'.join(parts)
