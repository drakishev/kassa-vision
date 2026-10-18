"""Durable SQLite queue. Only a lock-holding worker may recover running jobs."""
import json
import sqlite3
import time
from contextlib import contextmanager

from .schemas import DoneJob
from .cancellation import AnalysisCancelled, CANCELLED_MESSAGE


class QueueFull(Exception):
    pass


class JobStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, input_path TEXT NOT NULL, metadata TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT)""")
            db.execute("CREATE TABLE IF NOT EXISTS worker (id INTEGER PRIMARY KEY, data TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, job_id, input_path, metadata, max_pending=4):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= max_pending:
                raise QueueFull()
            db.execute("INSERT INTO jobs(job_id,input_path,metadata,state,created,updated) VALUES(?,?,?,'queued',?,?)",
                       (job_id, input_path, json.dumps(metadata), time.time(), time.time()))

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running', attempts=attempts+1, updated=? WHERE job_id=?",
                       (time.time(), row["job_id"]))
            result = dict(row)
            result["attempts"] += 1
            result["metadata"] = json.loads(result["metadata"])
            return result

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='failed', error='Processing interrupted repeatedly', updated=? WHERE state='running' AND attempts>=2", (time.time(),))
            db.execute("UPDATE jobs SET state='queued', updated=? WHERE state='running'", (time.time(),))

    def cancel(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET state='cancelled',error=?,updated=? WHERE job_id=? AND state IN ('queued','running')",
                       (CANCELLED_MESSAGE, time.time(), job_id))
        return self.public_job(job_id)

    def is_cancelled(self, job_id):
        with self.connect() as db:
            row=db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return bool(row and row['state']=='cancelled')

    def finish(self, job_id, result):
        result = DoneJob(job_id=job_id, **result).model_dump()
        with self.connect() as db:
            count = db.execute("UPDATE jobs SET state='done',result=?,updated=? WHERE job_id=? AND state='running'",
                               (json.dumps(result, ensure_ascii=False), time.time(), job_id)).rowcount
            if count != 1:
                row=db.execute("SELECT state FROM jobs WHERE job_id=?",(job_id,)).fetchone()
                if row and row['state']=='cancelled':
                    raise AnalysisCancelled()
                raise ValueError("Job is not running")

    def fail(self, job_id, error):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='failed',error=?,updated=? WHERE job_id=? AND state='running'",
                       (error, time.time(), job_id))

    def public_job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        if row["state"] == "done":
            return json.loads(row["result"])
        if row["state"] in {"failed", "cancelled"}:
            return {"job_id": job_id, "status": "failed", "error": row["error"]}
        return {"job_id": job_id, "status": "processing"}

    def heartbeat(self, state):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO worker VALUES(1,?)",
                       (json.dumps({"state": state, "heartbeat": time.time()}),))

    def worker_status(self):
        with self.connect() as db:
            row = db.execute("SELECT data FROM worker WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"state": "offline", "heartbeat": 0}
