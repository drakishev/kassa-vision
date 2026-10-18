import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from service.accounts import AccountStore, InvalidCredentials, LoginThrottled
from service.jobs import JobStore

PASSWORD='synthetic-password-42'

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=AccountStore(self.root/'accounts.sqlite3')

    def test_login_rotates_sessions_hashes_credentials_and_logout_revokes(self):
        user=self.store.create_user('tester',PASSWORD)
        first=self.store.login('tester',PASSWORD,'fixture-ip')
        self.assertEqual(self.store.user(first)['id'],user['id'])
        second=self.store.login('tester',PASSWORD,'fixture-ip',previous=first)
        self.assertIsNone(self.store.user(first));self.assertIsNotNone(self.store.user(second))
        self.store.logout(second);self.assertIsNone(self.store.user(second))
        raw=(self.root/'accounts.sqlite3').read_bytes()
        self.assertNotIn(PASSWORD.encode(),raw);self.assertNotIn(first.encode(),raw)

    def test_failed_login_is_generic_and_throttled(self):
        self.store.create_user('tester',PASSWORD)
        for _ in range(5):
            with self.assertRaises(InvalidCredentials):self.store.login('tester','wrong','fixture-ip')
        with self.assertRaises(LoginThrottled):self.store.login('tester',PASSWORD,'fixture-ip')
        with self.assertRaises(InvalidCredentials):self.store.login('unknown','wrong','other-ip')

    def test_expired_session_is_rejected(self):
        self.store.create_user('tester',PASSWORD);token=self.store.login('tester',PASSWORD,'fixture-ip')
        with sqlite3.connect(self.store.path) as db:db.execute('UPDATE sessions SET expires=0')
        self.assertIsNone(self.store.user(token))

    def test_history_is_isolated_persistent_paginated_and_has_no_image_payload(self):
        one=self.store.create_user('one',PASSWORD);two=self.store.create_user('two',PASSWORD)
        jobs=JobStore(self.root/'jobs.sqlite3')
        for job_id in ['a','b','c']:
            jobs.enqueue(job_id,'/synthetic.mov',{},max_pending=10)
            jobs.claim();jobs.finish(job_id,{'verdict':'no_violation','violations':[]})
        self.store.bind_job('a',one['id'],'first.mov');self.store.bind_job('b',two['id'],'second.mov')
        self.store.bind_job('c',one['id'],'last.mov')
        self.assertFalse(self.store.owns(two['id'],'a'))
        reopened=AccountStore(self.store.path)
        history=reopened.history(one['id'],jobs.path,limit=1,offset=0)
        self.assertEqual(history['total'],2);self.assertEqual(len(history['items']),1)
        self.assertNotIn('image_base64',json.dumps(history))
        self.assertEqual(history['items'][0]['status'],'done')
        self.assertEqual(reopened.history(two['id'],jobs.path)['total'],1)

    def test_initial_admin_preserves_legacy_jobs_once_without_overwriting_password(self):
        jobs=JobStore(self.root/'legacy #1?.sqlite3');jobs.enqueue('old','/old.mov',{})
        credentials=self.root/'admin-credentials.json'
        self.store.bootstrap(jobs.path,credentials)
        original=credentials.read_text();data=json.loads(original)
        token=self.store.login(data['username'],data['password'],'fixture-ip');admin=self.store.user(token)
        self.assertTrue(self.store.owns(admin['id'],'old'))
        self.store.bootstrap(jobs.path,credentials)
        self.assertEqual(credentials.read_text(),original)
        self.assertEqual(credentials.stat().st_mode&0o777,0o600)

    def test_history_accepts_database_paths_with_uri_reserved_characters(self):
        user=self.store.create_user('tester',PASSWORD)
        folder=self.root/'история #1?'
        folder.mkdir()
        jobs=JobStore(folder/'jobs.sqlite3')
        jobs.enqueue('special-path','/synthetic.mov',{})
        self.store.bind_job('special-path',user['id'],'synthetic.mov')
        result=self.store.history(user['id'],jobs.path)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['job_id'],'special-path')

    def test_history_does_not_create_missing_database(self):
        path=self.root/'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):
            self.store.history('unused-user',path)
        self.assertFalse(path.exists())

    def test_password_reset_revokes_all_sessions_and_old_password(self):
        self.store.create_user('tester',PASSWORD)
        token=self.store.login('tester',PASSWORD,'fixture-ip')
        self.store.reset_password('tester','new-synthetic-password-42')
        self.assertIsNone(self.store.user(token))
        with self.assertRaises(InvalidCredentials):self.store.login('tester',PASSWORD,'fixture-ip')
        self.assertIsNotNone(self.store.user(self.store.login('tester','new-synthetic-password-42','fixture-ip')))
