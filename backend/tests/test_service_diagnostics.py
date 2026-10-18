"""Synthetic diagnostics isolation, geometry and authorization checks."""
import json
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
from service.api import create_app
from service.config import Settings

class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(runtime=Path(self.tmp.name),api_key='synthetic-key')
        self.app=create_app(self.settings)
        self.client=TestClient(self.app)
        self.headers={'X-API-Key':'synthetic-key'}
        self.job='a'*32
        self.directory=self.settings.runtime/'jobs'/self.job
        self.run=self.directory/'attempt_1'
        (self.run/'frames').mkdir(parents=True)
        self.app.state.store.enqueue(self.job,str(self.directory/'input.mov'),{})
        self.app.state.store.claim()
        self.app.state.store.finish(self.job,{'verdict':'no_violation','violations':[]})
        Image.new('RGB',(32,24),'black').save(self.run/'frames/000000.jpg')
        self.payload={'pipeline':'fusion-v1','coordinates':{'detections':'saved-image pixels xyxy'},
            'frames':[{'frame_id':'f0000','time_sec':0,'width':32,'height':24,'image':'frames/000000.jpg',
                'detections':[],'poses':[],'states':[],'private_narrative':'secret fixture'}],
            'scene':{'anchor_time_sec':0,'scope':'anchor_frame_proposal_only','objects':[], 'masks':[], 'uncertainties':['private fixture']},
            'windows':[{'observations':['private fixture']}], 'source':{'path':'private fixture'}}
        self.save()
    def save(self): (self.run/'structured.json').write_text(json.dumps(self.payload))
    def tearDown(self): self.client.close();self.tmp.cleanup()
    def get(self,suffix,auth=True):
        return self.client.get('/api/v1/jobs/'+self.job+'/diagnostics'+suffix,headers=self.headers if auth else {})
    def test_safe_projection_has_geometry_but_no_narratives_or_paths(self):
        r=self.get('');self.assertEqual(r.status_code,200)
        d=r.json();self.assertEqual(d['frames'][0]['width'],32)
        self.assertNotIn('private',r.text);self.assertNotIn('image',d['frames'][0])
        self.assertEqual(d['capabilities']['detection'],'available')
        self.assertEqual(d['capabilities']['segmentation'],'available')
    def test_frames_are_exact_saved_jpeg_and_require_auth(self):
        self.assertEqual(self.get('/frames/0',False).status_code,401)
        r=self.get('/frames/0');self.assertEqual(r.status_code,200)
        self.assertEqual(r.content,(self.run/'frames/000000.jpg').read_bytes())
        self.assertEqual(r.headers['content-type'],'image/jpeg')
    def test_invalid_index_and_escaped_image_are_rejected(self):
        for value in ['-1','1','999999']:
            self.assertEqual(self.get('/frames/'+value).status_code,404)
        self.payload['frames'][0]['image']='../../outside.jpg';self.save()
        self.assertEqual(self.get('/frames/0').status_code,404)
    def test_missing_file_is_not_inferred_as_empty_detections(self):
        del self.payload['frames'][0]['detections'];del self.payload['scene']['masks'];self.save()
        d=self.get('').json()
        self.assertEqual(d['capabilities']['detection'],'unavailable')
        self.assertEqual(d['capabilities']['segmentation'],'unavailable')
    def test_missing_structured_and_in_progress_are_distinct(self):
        (self.run/'structured.json').unlink()
        self.assertEqual(self.get('').status_code,404)
        other='b'*32;self.app.state.store.enqueue(other,str(self.directory/'other.mov'),{})
        self.assertEqual(self.client.get('/api/v1/jobs/'+other+'/diagnostics',headers=self.headers).status_code,409)

    def test_legacy_uses_saved_evidence_without_inventing_layers(self):
        (self.run/'structured.json').unlink()
        legacy={'source':{'width':32,'height':24,'path':'private fixture'},
                'frames':[{'time_sec':0,'image':'frames/000000.jpg','detections':[],
                           'poses':[{'track_id':'person:1','keypoints':[[1,2,.8]]*133}]}]}
        (self.run/'evidence.json').write_text(json.dumps(legacy))
        response=self.get('');self.assertEqual(response.status_code,200)
        result=response.json();self.assertEqual(result['pipeline'],'legacy-supported')
        self.assertEqual(result['frames'][0]['width'],32)
        self.assertEqual(len(result['frames'][0]['poses'][0]['keypoints']),133)
        self.assertEqual(result['capabilities']['segmentation'],'unavailable')
        self.assertEqual(result['capabilities']['states'],'unavailable')
        self.assertNotIn('private',response.text)
        self.assertEqual(self.get('/frames/0').status_code,200)
        (self.run/'evidence.json').unlink()
        self.assertEqual(self.get('').status_code,404)
