"""Synthetic fixtures only: mapping must preserve observations and never invent reasons."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from analyze import CATALOG
from service.config import Settings
from service.events import public_violation
from service.jobs import JobStore
from service.presentation import enrich_review


class ReviewPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=Settings(runtime=Path(self.tmp.name),api_key='fixture-only')
        self.job_id='a'*32;self.folder=self.settings.runtime/'jobs'/self.job_id
        self.folder.mkdir();(self.folder/'input.mov').write_bytes(b'synthetic');self.run=self.folder/'attempt_1';self.run.mkdir()
        self.frames=[{'time_sec':1.0,'image':'test.jpg'}]
        (self.run/'test.jpg').write_bytes(b'\xff\xd8synthetic')
        self.event={'type':'V05','time_sec':1.2,'visible_evidence':'Синтетическое наблюдение.',
                    'explanation':'Синтетическая причина классификации.','confidence':'medium'}
        self.store=JobStore(self.settings.database)
        self.store.enqueue(self.job_id,str(self.folder/'input.mov'),{});self.store.claim()
        self.violation=public_violation(self.event,self.frames,self.run)
        self.result={'job_id':self.job_id,'status':'done','verdict':'violation','violations':[self.violation]}
        self.store.finish(self.job_id,{'verdict':'violation','violations':[self.violation]})
        (self.run/'evidence.json').write_text(json.dumps({'frames':self.frames}))
        (self.run/'window_000.json').write_text(json.dumps({'events':[self.event]}))

    def test_restores_model_class_observation_and_reason_without_changing_public_payload(self):
        original=copy.deepcopy(self.result)
        enriched=enrich_review(self.settings,self.job_id,self.result)
        detail=enriched['violations'][0]['review']
        self.assertEqual(detail['classification']['code'],'V05')
        self.assertEqual(detail['classification']['name'],CATALOG['V05'])
        self.assertEqual(detail['classification']['title'],'Деньги оставлены на рабочем столе')
        self.assertEqual(detail['observation'],self.event['visible_evidence'])
        self.assertEqual(detail['reason'],self.event['explanation'])
        self.assertIn('231',detail['normative']['reference'])
        self.assertIn('20',detail['normative']['reference'])
        self.assertIn('кассовых операций',detail['normative']['document_title'])
        self.assertEqual(detail['normative']['url'],'https://adilet.zan.kz/rus/docs/V1900019680')
        self.assertEqual(enriched['violations'][0]['evidence_frames'],self.violation['evidence_frames'])
        self.assertEqual(self.result,original)
        self.assertNotIn('review',self.store.public_job(self.job_id)['violations'][0])

    def test_missing_or_mismatched_model_data_never_creates_individual_reason(self):
        for events in [[],[{**self.event,'explanation':'Unrelated candidate'}]]:
            (self.run/'window_000.json').write_text(json.dumps({'events':events}))
            detail=enrich_review(self.settings,self.job_id,self.result)['violations'][0]['review']
            self.assertIsNone(detail['reason'])
            self.assertIsNone(detail['observation'])
            self.assertEqual(detail['classification']['code'],'V05')

    def test_unknown_class_has_no_invented_code_or_rule(self):
        self.result['violations'][0]['type']='Новый синтетический тип'
        detail=enrich_review(self.settings,self.job_id,self.result)['violations'][0]['review']
        self.assertIsNone(detail['classification']['code'])
        self.assertFalse(detail['classification']['in_catalog'])
        self.assertIsNone(detail['normative'])

    def test_duplicate_exact_matches_are_not_assigned_arbitrarily(self):
        duplicate=dict(self.event)
        # Far events can quantize to the same frame in a sparse legacy artifact.
        duplicate['time_sec']=5.0
        (self.run/'window_000.json').write_text(json.dumps({'events':[self.event,duplicate]}))
        detail=enrich_review(self.settings,self.job_id,self.result)['violations'][0]['review']
        self.assertIsNone(detail['reason'])

    def test_invalid_artifacts_fall_back_and_non_positive_statuses_stay_unchanged(self):
        (self.run/'evidence.json').write_text('broken')
        detail=enrich_review(self.settings,self.job_id,self.result)['violations'][0]['review']
        self.assertIsNone(detail['reason'])
        for result in [{'job_id':self.job_id,'status':'processing'},
                       {'job_id':self.job_id,'status':'failed','error':'Analysis cancelled by user'},
                       {'job_id':self.job_id,'status':'done','verdict':'no_violation','violations':[]}]:
            self.assertEqual(enrich_review(self.settings,self.job_id,result),result)
