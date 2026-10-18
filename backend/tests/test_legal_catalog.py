"""Public legal snapshots must match the recorded sources and analyzer usage."""
import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path

from service.events import RULES


class LegalCatalogTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[2] / 'frontend/public'

    def setUp(self):
        self.documents = json.loads((self.root / 'assets/legal-documents/catalog.json').read_text())['documents']

    def test_official_sources_are_unique_and_have_retrieval_dates(self):
        self.assertGreater(len(self.documents), 0)
        for field in ('id', 'code', 'url', 'snapshotUrl'):
            self.assertEqual(len(self.documents), len({doc[field] for doc in self.documents}))
        for doc in self.documents:
            self.assertEqual(doc['url'], 'https://adilet.zan.kz/rus/docs/' + doc['code'])
            self.assertIn(doc['category'], {'acts', 'amendments', 'historical'})
            self.assertTrue(datetime.fromisoformat(doc['capturedAt']).tzinfo)

    def test_every_download_is_the_recorded_snapshot(self):
        for doc in self.documents:
            with self.subTest(document=doc['code']):
                path = (self.root / doc['snapshotUrl'].lstrip('/')).resolve()
                self.assertTrue(path.is_relative_to(self.root.resolve() / 'assets/legal-documents'))
                raw = path.read_bytes()
                self.assertEqual(len(raw), doc['bytes'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), doc['sha256'])
                self.assertIn(doc['sha256'][:12], path.name)

    def test_usage_badges_match_the_analyzers_actual_rule_sources(self):
        self.assertEqual({row[1] for row in RULES.values()},
                         {doc['url'] for doc in self.documents if doc['usedByAnalyzer']})
