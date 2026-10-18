import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from annotation_service.nace_classify import load_records, questions_for, STATUS_CRITERIA


class ClassificationInputTests(unittest.TestCase):
    def test_current_episodes_override_stale_machine_description_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels, database = root / 'labels.json', root / 'annotations.sqlite3'
            labels.write_text(json.dumps([
                {'filename': 'a.mov', 'observed_action': 'obsolete machine description',
                 'label': 'violation', 'possible_violation_type': 'V13'},
                {'filename': 'b.mov', 'observed_action': 'person counts cash',
                 'description_source': 'local_model', 'limitations': 'sampled frames',
                 'label': 'no_violation', 'possible_violation_type': 'V05'}]))
            annotation = {'status': 'reviewed', 'reviewer': 'private reviewer',
                          'participants': [{'id': 'p1', 'name': 'private name', 'role': 'cashier'}],
                          'episodes': [{'id': 'ep1', 'action': 'count', 'item': 'cash',
                                        'note': 'Counting after client leaves', 'visibility': 'partial',
                                        'hypothesis': 'not yet established', 'explains_label': True}],
                          'label_check': {'status': 'explained', 'text': 'annotator interpretation'}}
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE annotations(video_id, revision, body, updated_at)')
                db.execute('INSERT INTO annotations VALUES(?,?,?,?)', (
                    hashlib.sha256(b'a.mov').hexdigest()[:20], 7, json.dumps(annotation), 'today'))
            before = database.read_bytes()
            rows = load_records(labels, database)
            self.assertEqual(database.read_bytes(), before)
            self.assertEqual(rows[0]['provenance']['revision'], 7)
            self.assertEqual(rows[0]['state']['episodes'][0]['visibility'], 'partial')
            self.assertEqual(rows[1]['state']['limitations'], 'sampled frames')
            state = json.dumps([r['state'] for r in rows])
            for excluded in ('private reviewer', 'private name', 'obsolete machine', 'V13', 'V05', 'explains_label'):
                self.assertNotIn(excluded, state)

    def test_missing_database_fails_instead_of_silently_falling_back(self):
        with tempfile.TemporaryDirectory() as directory:
            labels = Path(directory) / 'labels.json'
            labels.write_text('[{"filename":"a.mov"}]')
            with self.assertRaises(sqlite3.OperationalError):
                load_records(labels, Path(directory) / 'missing.sqlite3')
            self.assertEqual(len(load_records(labels, None)), 1)

    def test_duplicate_filenames_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            labels = Path(directory) / 'labels.json'
            labels.write_text('[{"filename":"a.mov"},{"filename":"a.mov"}]')
            with self.assertRaises(ValueError):
                load_records(labels, None)

    def test_catalog_questions_keep_uncertainty_and_multiple_categories(self):
        questions = questions_for({'V13': 'service-zone entry', 'V15': 'cash handover'})
        self.assertEqual(set(questions), {'V13', 'V15'})
        for code, question in questions.items():
            self.assertEqual(set(question['criteria']), set(STATUS_CRITERIA))
            self.assertIn({'V13': 'service-zone entry', 'V15': 'cash handover'}[code],
                          question['criteria']['described'])
            self.assertEqual(question['type'], 'choice')


if __name__ == '__main__':
    unittest.main()
