"""Synthetic frames only: choose a primary event, never truncate or force a positive."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from service.cancellation import AnalysisCancelled
from service.primary_event import parse_selection, select_primary_event
from service.inference import InferenceEngine
from analyze import CATALOG


class FakeVLM:
    def __init__(self, selected=1, verdict='violation'):
        self.selected=selected;self.verdict=verdict;self.calls=[]

    def generate(self, images, prompt, **options):
        self.calls.append((len(images),prompt,options))
        return json.dumps({'verdict':self.verdict,'selected_index':self.selected,
                           'reason':'Synthetic comparison.','uncertainties':[]}),{'runtime_sec':.01}


class PrimaryEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.run=Path(self.tmp.name)
        self.frames=[]
        for index in range(7):
            name=f'{index}.jpg';Image.new('RGB',(160,90),(index*20,30,40)).save(self.run/name)
            self.frames.append({'time_sec':index*2,'image':name})
        self.events=[{'type':'V05','time_sec':2,'visible_evidence':'Synthetic first event.',
                      'explanation':'Synthetic reason.','confidence':'high'},
                     {'type':'V13','time_sec':10,'visible_evidence':'Synthetic second event.',
                      'explanation':'Synthetic reason.','confidence':'low'}]

    def test_selects_verified_candidate_instead_of_first_or_highest_self_confidence(self):
        model=FakeVLM()
        selected,metrics=select_primary_event(self.events,self.frames,self.run,model)
        self.assertEqual(selected,[self.events[1]])
        self.assertEqual(metrics['input_candidates'],2)
        self.assertEqual(metrics['selected_events'],1)
        self.assertEqual(len(model.calls),1)
        self.assertEqual(model.calls[0][0],3)  # Whole-clip overview and two temporal strips.
        saved=json.loads((self.run/'primary_event.json').read_text())
        self.assertEqual(saved['decision']['selected_index'],1)
        self.assertEqual(saved['candidates'],self.events)
        self.assertIn('overview_times',json.loads((self.run/'primary_event_input.json').read_text()))

    def test_can_reject_all_candidates_without_forcing_one_violation(self):
        selected,metrics=select_primary_event(self.events,self.frames,self.run,FakeVLM(None,'no_violation'))
        self.assertEqual(selected,[])
        self.assertEqual(metrics['selected_events'],0)

    def test_single_candidate_is_still_verified(self):
        model=FakeVLM(None,'no_violation')
        selected,_=select_primary_event(self.events[:1],self.frames,self.run,model)
        self.assertEqual(selected,[])
        self.assertEqual(len(model.calls),1)

    def test_no_candidates_does_not_call_the_model(self):
        model=FakeVLM()
        selected,metrics=select_primary_event([],self.frames,self.run,model)
        self.assertEqual(selected,[]);self.assertEqual(model.calls,[])
        self.assertFalse(metrics['model_called'])

    def test_invalid_decisions_never_fall_back_to_first_event(self):
        for decision in [
            {'verdict':'violation','selected_index':2},
            {'verdict':'violation','selected_index':-1},
            {'verdict':'violation','selected_index':True},
            {'verdict':'violation','selected_index':None},
            {'verdict':'no_violation','selected_index':0},
            {'verdict':'unknown','selected_index':None},
        ]:
            with self.subTest(decision=decision),self.assertRaises(ValueError):
                parse_selection(json.dumps({**decision,'reason':'Synthetic.','uncertainties':[]}),2)
        with self.assertRaises(ValueError):parse_selection('{"verdict":',2)

    def test_parser_accepts_fenced_json(self):
        raw='```json\n'+json.dumps({'verdict':'violation','selected_index':1,
                                  'reason':'Synthetic.','uncertainties':[]})+'\n```'
        self.assertEqual(parse_selection(raw,2)['selected_index'],1)

    def test_missing_index_is_not_an_implicit_negative(self):
        with self.assertRaises(ValueError):
            parse_selection(json.dumps({'verdict':'no_violation','reason':'Synthetic.','uncertainties':[]}),2)

    def test_deadline_and_cancellation_are_preserved(self):
        model=FakeVLM()
        with self.assertRaises(TimeoutError):
            select_primary_event(self.events,self.frames,self.run,model,deadline=time.monotonic()-1)
        def cancelled():raise AnalysisCancelled()
        with self.assertRaises(AnalysisCancelled):
            select_primary_event(self.events,self.frames,self.run,model,check_cancelled=cancelled)
        self.assertEqual(model.calls,[])

    def test_model_failure_is_not_reported_as_no_violation(self):
        class BrokenVLM:
            def generate(self,*args,**kwargs):raise TimeoutError('Synthetic deadline.')
        with self.assertRaises(TimeoutError):
            select_primary_event(self.events,self.frames,self.run,BrokenVLM())
        self.assertFalse((self.run/'primary_event.json').exists())

    def test_missing_frames_are_not_an_empty_negative_result(self):
        with self.assertRaises(ValueError):
            select_primary_event(self.events,[],self.run,FakeVLM())

    def test_cancel_after_generation_does_not_publish_a_decision(self):
        cancelled=False
        def guard():
            if cancelled:raise AnalysisCancelled()
        class CancellingVLM(FakeVLM):
            def generate(inner,*args,**kwargs):
                nonlocal cancelled
                result=super().generate(*args,**kwargs);cancelled=True;return result
        with self.assertRaises(AnalysisCancelled):
            select_primary_event(self.events,self.frames,self.run,CancellingVLM(),check_cancelled=guard)
        self.assertFalse((self.run/'primary_event.json').exists())

    def test_full_pipeline_returns_one_selected_event_and_keeps_all_window_candidates(self):
        class PipelineVLM(FakeVLM):
            def generate(inner,images,prompt,**options):
                if not inner.calls:
                    inner.calls.append((len(images),prompt,options))
                    return json.dumps({'verdict':'violation','events':self.events,'uncertainties':[]}),{'runtime_sec':.01}
                return super().generate(images,prompt,**options)
        engine=InferenceEngine.__new__(InferenceEngine)
        engine.settings=SimpleNamespace(max_job_sec=285,fps=1,window_sec=12,overlap_sec=2,frame_count=6)
        engine.perception=object();engine.vlm=PipelineVLM();engine.load_sec=0
        folder=self.run/'job';folder.mkdir();run=folder/'attempt_1';run.mkdir()
        frames=[]
        for original in self.frames:
            name=original['image'];(run/name).write_bytes((self.run/name).read_bytes())
            frames.append({**original,'detections':[],'poses':[],'relations':[]})
        evidence={'frames':frames,'sampling':{'coverage':'full_clip'},'signals':[],'limitations':[]}
        job={'input_path':str(folder/'input.mov'),'attempts':1,'metadata':{'duration_sec':12}}
        with patch('perception.process_video',return_value=(run,evidence,{})):
            result=engine.analyze(job)
        self.assertEqual(result['verdict'],'violation')
        self.assertEqual(len(result['violations']),1)
        self.assertEqual(result['violations'][0]['type'],CATALOG['V13'])
        self.assertEqual(result['violations'][0]['timecode_sec'],10)
        self.assertEqual(len(json.loads((run/'window_000.json').read_text())['events']),2)
        self.assertEqual(json.loads((folder/'processing_metrics.json').read_text())['primary_event_selection']['selected_events'],1)


if __name__=='__main__':unittest.main()
