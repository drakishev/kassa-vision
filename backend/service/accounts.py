"""Local password accounts, expiring server sessions and durable job ownership."""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SESSION_TTL = 8 * 60 * 60
TOKEN = re.compile(r'^[A-Za-z0-9_-]{43}$')
USERNAME = re.compile(r'^[a-z0-9][a-z0-9_.-]{2,31}$')

class InvalidCredentials(Exception):pass
class LoginThrottled(Exception):pass


def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=salt,n=32768,r=8,p=1,
                          maxmem=64*1024*1024,dklen=64)


class AccountStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,
                    salt BLOB NOT NULL,password_hash BLOB NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,
                    expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS job_owners(job_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
                    filename TEXT,created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS owned_jobs ON job_owners(user_id,created DESC);
                CREATE TABLE IF NOT EXISTS login_limits(scope TEXT PRIMARY KEY,attempts INTEGER NOT NULL,
                    started REAL NOT NULL);
            ''')
        os.chmod(self.path,0o600)

    @contextmanager
    def connect(self):
        # ATTACH must honor mode=ro even on SQLite builds without USE_URI.
        db=sqlite3.connect(self.path,timeout=15,uri=True);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _credentials(username,password):
        username=username.strip().lower()
        if not USERNAME.fullmatch(username):raise ValueError('Логин: 3–32 латинские буквы, цифры, точка, дефис или подчёркивание.')
        if not 12<=len(password)<=128:raise ValueError('Пароль должен содержать от 12 до 128 символов.')
        return username

    @staticmethod
    def _insert_user(db,username,password):
        salt=secrets.token_bytes(16);user_id=secrets.token_hex(16)
        db.execute('INSERT INTO users VALUES(?,?,?,?,?)',
                   (user_id,username,salt,password_hash(password,salt),time.time()))
        return {'id':user_id,'username':username}

    def create_user(self,username,password):
        username=self._credentials(username,password)
        with self.connect() as db:return self._insert_user(db,username,password)

    def bootstrap(self,jobs_path,credentials_path):
        """One initial administrator owns the archive made before accounts existed."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():return
            credentials_path=Path(credentials_path)
            if credentials_path.exists():credentials=json.loads(credentials_path.read_text())
            else:
                credentials={'username':'admin','password':secrets.token_urlsafe(24)}
                fd=os.open(credentials_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'w') as file:json.dump(credentials,file)
            username=self._credentials(credentials['username'],credentials['password'])
            admin=self._insert_user(db,username,credentials['password'])
            with sqlite3.connect(Path(jobs_path).resolve().as_uri()+'?mode=ro',uri=True) as source:
                old=source.execute('SELECT job_id,created FROM jobs').fetchall()
            db.executemany('INSERT OR IGNORE INTO job_owners VALUES(?,?,NULL,?)',
                           [(job_id,admin['id'],created) for job_id,created in old])

    def _reserve_login(self,username,address):
        now=time.time();scopes=[('user:'+username,5),('ip:'+address,20)]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_limits WHERE started<?',(now-300,))
            keys=[(hashlib.sha256(scope.encode()).hexdigest(),limit) for scope,limit in scopes]
            for scope,limit in keys:
                row=db.execute('SELECT attempts FROM login_limits WHERE scope=?',(scope,)).fetchone()
                if row and row['attempts']>=limit:raise LoginThrottled()
            for scope,_ in keys:
                db.execute('INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(scope) DO UPDATE SET attempts=attempts+1', (scope,now))
        return [scope for scope,_ in keys]

    def login(self,username,password,address,previous=None):
        username=username.strip().lower()
        scopes=self._reserve_login(username,address)
        with self.connect() as db:
            row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        salt=row['salt'] if row else bytes(16)
        expected=row['password_hash'] if row else bytes(64)
        if not hmac.compare_digest(password_hash(password,salt),expected) or row is None:
            raise InvalidCredentials()
        token=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<=?',(time.time(),))
            if previous and TOKEN.fullmatch(previous):
                db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(previous.encode()).hexdigest(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),row['id'],time.time()+SESSION_TTL))
            db.executemany('DELETE FROM login_limits WHERE scope=?',[(scope,) for scope in scopes])
        return token

    def user(self,token):
        if not token or not TOKEN.fullmatch(token):return None
        with self.connect() as db:
            row=db.execute('SELECT u.id,u.username FROM sessions s JOIN users u ON u.id=s.user_id '
                           'WHERE s.token_hash=? AND s.expires>?',
                           (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return dict(row) if row else None

    def logout(self,token):
        if not token or not TOKEN.fullmatch(token):return
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def reset_password(self,username,password):
        username=self._credentials(username,password);salt=secrets.token_bytes(16)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
            if not row:raise ValueError('Пользователь не найден.')
            db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',
                       (salt,password_hash(password,salt),row['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))

        initial=self.path.parent/'admin-credentials.json'
        if initial.exists():
            try:
                if json.loads(initial.read_text()).get('username')==username:initial.unlink()
            except (OSError,ValueError):pass

    def bind_job(self,job_id,user_id,filename):
        filename=''.join(c for c in filename.replace('\\','/').split('/')[-1] if c.isprintable())[:255]
        with self.connect() as db:
            db.execute('INSERT INTO job_owners VALUES(?,?,?,?)',(job_id,user_id,filename,time.time()))

    def owns(self,user_id,job_id):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM job_owners WHERE job_id=? AND user_id=?',(job_id,user_id)).fetchone() is not None

    def filename(self,user_id,job_id):
        with self.connect() as db:
            row=db.execute('SELECT filename FROM job_owners WHERE job_id=? AND user_id=?',(job_id,user_id)).fetchone()
        return row['filename'] if row else None

    def history(self,user_id,jobs_path,limit=20,offset=0):
        with self.connect() as db:
            db.execute('ATTACH DATABASE ? AS analyses',(Path(jobs_path).resolve().as_uri()+'?mode=ro',))
            total=db.execute('SELECT count(*) FROM job_owners o JOIN analyses.jobs j USING(job_id) WHERE o.user_id=?',(user_id,)).fetchone()[0]
            rows=db.execute("""SELECT j.job_id,o.filename,j.created,j.updated,
                CASE WHEN j.state IN ('queued','running') THEN 'processing' ELSE j.state END AS status,
                CASE WHEN j.state='done' THEN json_extract(j.result,'$.verdict') END AS verdict,
                CASE WHEN j.state='done' THEN json_array_length(j.result,'$.violations') END AS event_count
                FROM job_owners o JOIN analyses.jobs j USING(job_id) WHERE o.user_id=?
                ORDER BY j.created DESC,j.job_id DESC LIMIT ? OFFSET ?""",(user_id,limit,offset)).fetchall()
        return {'items':[dict(row) for row in rows],'total':total,'limit':limit,'offset':offset}


def main():
    from .config import Settings
    parser=argparse.ArgumentParser(description='Local website accounts; passwords never belong in command arguments.')
    parser.add_argument('action',choices=['create-user','reset-password']);parser.add_argument('username')
    args=parser.parse_args()
    password=getpass.getpass('Новый пароль (12–128 символов): ')
    if getpass.getpass('Повторите пароль: ')!=password:raise SystemExit('Пароли не совпадают.')
    settings=Settings();store=AccountStore(settings.runtime/'ui/sessions.sqlite3')
    try:
        if args.action=='create-user':store.create_user(args.username,password)
        else:store.reset_password(args.username,password)
    except (ValueError,sqlite3.IntegrityError) as error:raise SystemExit(str(error))
    print('Учётная запись обновлена. При смене пароля прежние сессии завершены.')

if __name__=='__main__':main()
