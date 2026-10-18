import unittest
from annotation_service.schema import Annotation
from annotation_service.preannotation.verify import structural, crossing


class VerificationTests(unittest.TestCase):
    def test_conflict_and_absence_not_promoted(self):
        a = Annotation(objects=[{'id':'safe', 'label':'safe', 'keyframes':[{'time':1,'box':[.1,.1,.3,.3]}]}],
                       zones=[{'id':'zone','label':'storage','points':[[0,0],[1,0],[1,1]],'confirmed':False}],
                       states=[{'id':'s1','target_id':'safe','value':'open','start':0,'end':3},
                               {'id':'s2','target_id':'safe','value':'closed','start':2,'end':4},
                               {'id':'s3','target_id':'zone','value':'person_absent','start':0,'end':4,'fully_visible':True}]).model_dump()
        d = {'annotation':a,'coverage':{'duration':5},'metadata':{}}
        r = structural(d)
        self.assertEqual(1,sum(x['code']=='contradictory_states' for x in r['issues']))
        self.assertIn('absence_needs_continuous_visibility', [x['code'] for x in r['issues']])
        self.assertFalse(d['annotation']['full_video_reviewed'])
        self.assertFalse(d['annotation']['zones'][0]['confirmed'])

    def test_mask_and_time_checked(self):
        a = Annotation(objects=[{'id':'o','label':'safe','keyframes':[{'time':6,'box':[.1,.1,.2,.2],
                            'polygons':[[[0,0],[1,0],[1,1]]]}]}]).model_dump()
        r = structural({'annotation':a,'coverage':{'duration':5},'metadata':{}})
        self.assertEqual({'keyframe_outside_video','mask_outside_box'}, {x['code'] for x in r['issues']})
        self.assertEqual(1,r['counts']['mask_keyframes'])

    def test_crossing(self):
        self.assertTrue(crossing([[0,0],[1,1],[1,0],[0,1]]))
        self.assertFalse(crossing([[0,0],[1,0],[1,1],[0,1]]))



class SemanticVerificationTests(unittest.TestCase):
    def test_missing_or_duplicated_checks_fail(self):
        from annotation_service.preannotation.semantic_verify import validate_response
        task={'kind':'objects','frames':[{'time':0}],'items':[{'id':'one','label':'person'},{'id':'two','label':'cash'}]}
        with self.assertRaises(ValueError):
            validate_response({'checks':[{'id':'P1','status':'supported','evidence_times':[0]}]},task,{'one':'P1','two':'P2'})
        with self.assertRaises(ValueError):
            validate_response({'checks':[{'id':'P1','status':'supported','evidence_times':[0]}]*2},task,{'one':'P1','two':'P2'})

    def test_cash_requires_each_visible_phase(self):
        from annotation_service.preannotation.semantic_verify import validate_response
        task={'kind':'events','frames':[{'time':0}],'items':[{'id':'transfer','label':'cash_transfer'}]}
        result=validate_response({'checks':[{'id':'P1','status':'supported','evidence_times':[0],
            'cash_visible':True,'release_receive_visible':False,'different_people':True}]},task,{'transfer':'P1'})
        self.assertEqual('uncertain',result['checks'][0]['status'])

    def test_invented_evidence_rejected(self):
        from annotation_service.preannotation.semantic_verify import validate_response
        task={'kind':'objects','frames':[{'time':0},{'time':2}],'items':[{'id':'o','label':'safe'}]}
        with self.assertRaises(ValueError):
            validate_response({'checks':[{'id':'P1','status':'supported','evidence_times':[1]}]},task,{'o':'P1'})

class AuditRepairTests(unittest.TestCase):
    def test_nonfinite_geometry_rejected_before_clamping(self):
        from annotation_service.preannotation.common import bbox, validate_geometry
        for value in (float('nan'),float('inf'),float('-inf')):
            with self.assertRaises(ValueError):bbox([0,0,value,500])
            a,_,rejected=validate_geometry({'zones':[{'label':'storage','points':[[0,0],[value,0],[500,500]]}]},0,'x')
            self.assertEqual([],a['zones']);self.assertEqual(1,len(rejected))

    def test_cache_distinguishes_image_and_budget(self):
        from annotation_service.preannotation.worker import invoke
        from PIL import Image
        from pathlib import Path
        import tempfile
        class Fake:
            calls=0
            def generate(self,images,prompt,**kwargs):
                self.calls+=1
                return '{"answer":'+str(self.calls)+'}',{}
        model=Fake()
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            red,blue=Image.new('RGB',(8,8),'red'),Image.new('RGB',(8,8),'blue')
            self.assertEqual(1,invoke(model,[red],'same',folder,'test',10)['answer'])
            self.assertEqual(1,invoke(model,[red],'same',folder,'test',10)['answer'])
            self.assertEqual(2,invoke(model,[blue],'same',folder,'test',10)['answer'])
            self.assertEqual(3,invoke(model,[blue],'same',folder,'test',20)['answer'])
            self.assertEqual(3,model.calls)

    def test_empty_declared_sampling_fails(self):
        from annotation_service.preannotation.audit import sampling_issues
        from annotation_service.preannotation.common import write_json
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);write_json(home/'frames/frames.json',{'frames':[]})
            self.assertIn('empty or invalid decoded timestamps',sampling_issues(home,{'duration':1,'sample_times':[]},home/'missing.mp4'))

class EvidenceScopeTests(unittest.TestCase):
    def test_mask_cannot_use_another_candidate_frame(self):
        from annotation_service.preannotation.summarize_verification import normalize_checks
        task={'kind':'masks','frames':[{'time':0},{'time':10}],'items':[{'id':'m','time':10}]}
        out=normalize_checks({'checks':[{'id':'P1','status':'supported','evidence_times':[0]}]},task,{})
        self.assertEqual('uncertain',out[0]['status'])

    def test_absence_never_approved_from_samples(self):
        from annotation_service.preannotation.summarize_verification import normalize_checks
        task={'kind':'states','frames':[{'time':0},{'time':10}],'items':[{'id':'s','start':0,'end':10,'value':'person_absent'}]}
        out=normalize_checks({'checks':[{'id':'P1','status':'supported','evidence_times':[0,10]}]},task,{})
        self.assertEqual('uncertain',out[0]['status'])
        self.assertIn('continuous_visibility_not_established',out[0]['limits'])

    def test_event_evidence_outside_own_interval_not_accepted(self):
        from annotation_service.preannotation.summarize_verification import normalize_checks
        task={'kind':'events','frames':[{'time':0},{'time':1},{'time':10}],'items':[{'id':'e','start':10,'end':11,'label':'door_open'}]}
        out=normalize_checks({'checks':[{'id':'P1','status':'supported','evidence_times':[0,1]}]},task,{})
        self.assertEqual('uncertain',out[0]['status'])

    def test_valid_evidence_stays_candidate_not_human_label(self):
        from annotation_service.preannotation.summarize_verification import normalize_checks
        task={'kind':'events','frames':[{'time':10},{'time':11}],'items':[{'id':'e','start':10,'end':11,'label':'door_open'}]}
        out=normalize_checks({'checks':[{'id':'P1','status':'supported','evidence_times':[10,11]}]},task,{})
        self.assertEqual('supported',out[0]['status']);self.assertEqual('weak_event_window',out[0]['scope'])

class DurationIntegrityTests(unittest.TestCase):
    def test_nan_duration_rejected(self):
        with self.assertRaises(ValueError):
            structural({'annotation':Annotation().model_dump(),'coverage':{'duration':float('nan')}})

    def test_decoded_times_beyond_end_rejected(self):
        import tempfile
        from pathlib import Path
        from annotation_service.preannotation.common import write_json
        from annotation_service.preannotation.audit import sampling_issues
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);write_json(home/'frames/frames.json',{'frames':[{'time':0,'file':'0.jpg'},{'time':1.5,'file':'1.jpg'}]})
            issues=sampling_issues(home,{'duration':1,'sample_times':[0,1.5]},home/'v.mp4')
            self.assertIn('empty or invalid decoded timestamps',issues)

class ResponseBindingTests(unittest.TestCase):
    def test_finalizer_uses_recorded_attempt_not_old_retry(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from annotation_service.preannotation.common import write_json
        from annotation_service.preannotation.summarize_verification import read_response
        task={'kind':'zones','frames':[{'time':0}],'items':[{'id':'z','label':'storage'}]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);signature={'task':task,'version':'fixture'}
            write_json(root/'request.json',signature)
            key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
            def raw(status):return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'checks':[{'id':'P1','status':status,'evidence_times':[0]}]})}}]}
            write_json(root/'raw_0.json',raw('uncertain'));write_json(root/'raw_1.json',raw('supported'))
            write_json(root/'result.json',{'status':'done','attempt':0,'request_hash':key})
            response,error=read_response(root,task,{})
            self.assertIsNone(error);self.assertEqual('uncertain',response['checks'][0]['status'])
            write_json(root/'result.json',{'status':'done','attempt':0,'request_hash':'wrong'})
            self.assertEqual('response_request_mismatch',read_response(root,task,{})[1])

    def test_missing_last_attempt_never_uses_previous_answer(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from annotation_service.preannotation.common import write_json
        from annotation_service.preannotation.summarize_verification import read_response
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);signature={'version':'fixture'};write_json(root/'request.json',signature)
            key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
            write_json(root/'raw_0.json',{'anything':'previous'})
            write_json(root/'result.json',{'status':'failed','errors':[{},{}],'request_hash':key})
            self.assertEqual('no_model_response_for_last_attempt',read_response(root,{},{} )[1])

class ReferenceVisibilityTests(unittest.TestCase):
    def test_event_cannot_reuse_person_track_from_different_time(self):
        from annotation_service.preannotation.summarize_verification import normalize_checks
        task={'kind':'events','frames':[{'time':0},{'time':10},{'time':11}],
              'items':[{'id':'e','start':10,'end':11,'label':'phone_use','actor_id':'person'}]}
        a={'objects':[{'id':'person','label':'person','keyframes':[{'time':0,'box':[0,0,.1,.1]}]}]}
        out=normalize_checks({'checks':[{'id':'P1','status':'supported','evidence_times':[10,11]}]},task,a)
        self.assertEqual('uncertain',out[0]['status'])

    def test_malformed_model_rows_fail_explicitly(self):
        from annotation_service.preannotation.summarize_verification import normalize_checks
        task={'kind':'zones','frames':[{'time':0}],'items':[{'id':'z'}]}
        with self.assertRaises(ValueError):normalize_checks({'checks':[5]},task,{})

class PointStateTests(unittest.TestCase):
    def test_instant_state_conflicts_with_interval_interior(self):
        a=Annotation(objects=[{'id':'o','label':'safe'}],states=[
            {'id':'s1','target_id':'o','value':'open','start':1,'end':1},
            {'id':'s2','target_id':'o','value':'closed','start':0,'end':2}]).model_dump()
        r=structural({'annotation':a,'coverage':{'duration':3},'metadata':{}})
        self.assertIn('contradictory_states',[i['code'] for i in r['issues']])

    def test_clean_boundary_transition_not_flagged(self):
        a=Annotation(objects=[{'id':'o','label':'safe'}],states=[
            {'id':'s1','target_id':'o','value':'open','start':0,'end':1},
            {'id':'s2','target_id':'o','value':'closed','start':1,'end':2}]).model_dump()
        r=structural({'annotation':a,'coverage':{'duration':3},'metadata':{}})
        self.assertNotIn('contradictory_states',[i['code'] for i in r['issues']])

    def test_empty_corpus_cannot_report_completion(self):
        import tempfile
        from pathlib import Path
        from annotation_service.preannotation.common import write_json
        from annotation_service.preannotation.summarize_verification import summarize
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_json(root/'inventory.json',[{'filename':'expected.mp4'}])
            with self.assertRaises(ValueError):summarize(root/'absent',root/'out',root/'inventory.json')

class PolygonTopologyTests(unittest.TestCase):
    def test_retraced_edge_is_not_a_simple_polygon(self):
        self.assertTrue(crossing([[0,0],[1,0],[.5,0],[.5,1],[0,1]]))
    def test_explicitly_closed_simple_ring_is_allowed(self):
        self.assertFalse(crossing([[0,0],[1,0],[1,1],[0,1],[0,0]]))

if __name__=='__main__':unittest.main()
