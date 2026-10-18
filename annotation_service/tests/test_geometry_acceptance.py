"""Malformed proposals remain inspectable but cannot become training labels."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from annotation_service.app import create_app
from annotation_service.drafts import DraftStore
from annotation_service.preannotation.verify import structural
from annotation_service.tests.test_service import annotation, make_fixture

CROSSING = [[.1, .1], [.9, .9], [.1, .9], [.8, .1]]  # nonzero signed area
COLLINEAR = [[.1, .1], [.2, .2], [.3, .3]]


class GeometryAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset_tmp = tempfile.TemporaryDirectory()
        cls.dataset = make_fixture(Path(cls.dataset_tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.dataset_tmp.cleanup()

    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(self.dataset, self.runtime.name))
        self.vid = self.client.get('/api/videos').json()[0]['id']
        self.dbpath = Path(self.runtime.name) / 'annotations.sqlite3'

    def tearDown(self):
        self.client.close()
        self.runtime.cleanup()

    def invalid(self, kind='mask', polygon=CROSSING):
        a = annotation()
        if kind == 'zone':
            a['zones'][0]['points'] = polygon
        else:
            a['objects'][0]['keyframes'][0].update(box=[0, 0, 1, 1], polygons=[polygon])
        return a

    def test_save_rejects_crossing_zone_and_degenerate_or_crossing_mask(self):
        for kind, polygon in [('zone', CROSSING), ('mask', CROSSING), ('mask', COLLINEAR)]:
            with self.subTest(kind=kind, polygon=polygon):
                response = self.client.put(f'/api/videos/{self.vid}/annotation', json={
                    'revision': 0, 'annotation': self.invalid(kind, polygon)})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get(f'/api/videos/{self.vid}').json()['revision'], 0)

    def test_invalid_draft_can_be_audited_rejected_or_corrected_but_not_accepted(self):
        store = DraftStore(self.dbpath)
        a = self.invalid()
        store.publish(self.vid, {'run_id': 'geometry', 'status': 'done', 'annotation': a})
        report = structural({'annotation': a, 'coverage': {'duration': 40}})
        self.assertIn('self_intersecting_polygon', [x['code'] for x in report['issues']])
        payload = {'revision': 0, 'draft_revision': 1, 'action': 'accept', 'ids': ['o'],
                   'reviewer': 'QA', 'confirm_zones': True}
        response = self.client.post(f'/api/videos/{self.vid}/draft/review', json=payload)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(store.get(self.vid)['reviews'], {})
        self.assertEqual(self.client.get(f'/api/videos/{self.vid}').json()['revision'], 0)
        fixed = annotation()['objects'][0]
        response = self.client.post(f'/api/videos/{self.vid}/draft/review', json={
            **payload, 'overrides': {'o': fixed}})
        self.assertEqual(response.status_code, 200, response.text)

    def test_invalid_draft_can_be_rejected(self):
        store = DraftStore(self.dbpath)
        store.publish(self.vid, {'run_id': 'geometry', 'annotation': self.invalid('zone')})
        response = self.client.post(f'/api/videos/{self.vid}/draft/review', json={
            'revision': 0, 'draft_revision': 1, 'action': 'reject', 'ids': ['z'], 'reviewer': 'QA'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_legacy_invalid_labels_are_readable_but_coco_export_is_blocked(self):
        a = self.invalid()
        a.update(status='reviewed', reviewer='QA', verdict='uncertain', full_video_reviewed=True)
        with sqlite3.connect(self.dbpath) as db:
            db.execute('INSERT INTO annotations VALUES(?,?,?,?)', (self.vid, 1, json.dumps(a), 'old'))
        self.assertEqual(self.client.get('/api/export').status_code, 200)
        response = self.client.get('/api/export/coco')
        self.assertEqual(response.status_code, 422, response.text)

    def test_closed_simple_contour_can_be_saved(self):
        a = self.invalid(polygon=[[.1,.1],[.3,.1],[.3,.4],[.1,.4],[.1,.1]])
        response = self.client.put(f'/api/videos/{self.vid}/annotation', json={'revision': 0, 'annotation': a})
        self.assertEqual(response.status_code, 200, response.text)
