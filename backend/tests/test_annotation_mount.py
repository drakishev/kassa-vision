"""The integrated editor protects its API and resolves media below its mount."""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from annotation_service.tests.test_service import make_fixture, annotation
from service.accounts import AccountStore
from service.config import Settings
from service.ui_gateway import create_ui


class AnnotationMountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(runtime=root / 'runtime', api_key='synthetic-api-key')
        accounts = AccountStore(self.settings.runtime / 'ui/sessions.sqlite3')
        accounts.create_user('tester', 'synthetic-password-42')
        self.dataset = make_fixture(root / 'dataset')
        self.client = TestClient(create_ui(self.settings, annotation_dataset=self.dataset))
        self.addCleanup(self.client.close)

    def login(self):
        response = self.client.post('/ui/api/auth/login', json={
            'username': 'tester', 'password': 'synthetic-password-42'})
        self.assertEqual(response.status_code, 200)

    def test_no_annotation_data_or_files_without_login(self):
        self.assertEqual(self.client.get('/annotate/', follow_redirects=False).status_code, 303)
        for path in ['api/videos', 'api/export', 'api/export/episodes', 'app.js']:
            self.assertEqual(self.client.get('/annotate/' + path).status_code, 401)
        self.assertEqual(self.client.put('/annotate/api/videos/unknown/annotation', json={}).status_code, 401)

    def test_saved_annotations_and_export_use_mount_prefix(self):
        self.login()
        page = self.client.get('/annotate/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('src="./app.js"', page.text)
        for resource in ['app.js', 'style.css', 'episodes.js', 'drafts.js']:
            self.assertEqual(self.client.get('/annotate/' + resource).status_code, 200)
        videos = self.client.get('/annotate/api/videos').json()
        vid = videos[0]['id']
        body = annotation()
        body.update(status='reviewed', reviewer='Synthetic reviewer',
                    verdict='no_violation', full_video_reviewed=True)
        saved = self.client.put(f'/annotate/api/videos/{vid}/annotation', json={
            'revision': 0, 'annotation': body})
        self.assertEqual(saved.status_code, 200, saved.text)
        export = self.client.get('/annotate/api/export/coco').json()
        frame = export['images'][0]['frame_url']
        self.assertTrue(frame.startswith('/annotate/api/videos/'))
        self.assertEqual(self.client.get(frame).status_code, 200)
        media = self.client.get(f'/annotate/api/videos/{vid}/media', headers={'Range': 'bytes=0-15'})
        self.assertEqual(media.status_code, 206)
        self.assertEqual(len(media.content), 16)
        rejected = self.client.put(f'/annotate/api/videos/{vid}/annotation',
                                   headers={'Origin': 'https://untrusted.example'},
                                   json={'revision': 1, 'annotation': body})
        self.assertEqual(rejected.status_code, 403)
        self.client.post('/ui/api/auth/logout')
        self.assertEqual(self.client.get(frame).status_code, 401)

    def test_missing_dataset_is_an_empty_workspace(self):
        from annotation_service.app import create_app
        with TestClient(create_app(Path(self.temp.name) / 'missing',
                                   Path(self.temp.name) / 'separate-runtime')) as client:
            self.assertEqual(client.get('/api/videos').json(), [])
