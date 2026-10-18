import copy,json,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from annotation_service.app import create_app
from annotation_service.schema import Annotation
from annotation_service.rules import candidates


def make_fixture(root):
    root=Path(root);(root/'open/videos').mkdir(parents=True,exist_ok=True);(root/'reference').mkdir(exist_ok=True);(root/'audit').mkdir(exist_ok=True)
    path=root/'open/videos/demo-1.mp4'
    if not path.exists():
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0x293f40:s=640x360:r=10:d=40','-vf','drawbox=x=40:y=80:w=160:h=230:color=0x87a994:t=fill,drawbox=x=270:y=190:w=270:h=90:color=0xb69269:t=fill,drawbox=x=560:y=50:w=65:h=280:color=0x728899:t=fill','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(path)],check=True)
    other=root/'open/videos/demo-2.mp4'
    if not other.exists(): other.write_bytes(path.read_bytes())
    rows=[{'filename':f'demo-{i}.mp4','label':'синтетический пример','sha256':'fixture-group','timecode_sec':8} for i in [1,2]]
    (root/'reference/source_labels.json').write_text(json.dumps(rows))
    ai=[{**r,'observed_action':'Искусственный ролик для проверки интерфейса. <img src=x onerror=alert(1)>','explanation':'Сейф слева, рабочая поверхность в центре, дверь справа. Это тестовые прямоугольники.','timecode_sec':8,'group_id':'synthetic','fold':'1'} for r in rows]
    (root/'reference/labels_final.json').write_text(json.dumps(ai))
    (root/'audit/labels_annotated.json').write_text(json.dumps(ai))
    return root

def annotation():
    a=Annotation().model_dump();a['status']='in_progress'
    a['zones']=[{'id':'z','label':'cashier','name':'Касса','points':[[0,0],[1,0],[1,1],[0,1]],'anchors':['o'],'confirmed':True}]
    a['objects']=[{'id':'o','label':'safe','name':'Сейф','role':'unknown','zone_id':'z','keyframes':[{'time':0,'box':[.1,.1,.2,.3]}],'interpolate':False}]
    a['states']=[{'id':'s1','target_id':'o','value':'open','start':0,'end':35,'note':'','fully_visible':True},{'id':'s2','target_id':'z','value':'person_absent','start':5,'end':30,'note':'','fully_visible':True}]
    return a

class RulesTests(unittest.TestCase):
    def test_absence_must_be_explicit(self):
        a=annotation();a['states']=a['states'][:1];self.assertEqual(candidates(a),[])
    def test_overlap_and_threshold(self):
        a=annotation();r=candidates(a);self.assertEqual([(x['start'],x['end']) for x in r],[(5,30)])
        a['thresholds']['safe']=26;self.assertEqual(candidates(a),[])
    def test_unknown_breaks_and_contiguous_spans_merge(self):
        a=annotation();a['states'].append({'id':'s3','target_id':'z','value':'unknown','start':15,'end':20,'fully_visible':False});self.assertEqual(candidates(a),[])
        a['states'].pop();a['states'][1]['end']=18;a['states'].append({**a['states'][1],'id':'s4','start':18,'end':30});self.assertEqual(len(candidates(a)),1)
    def test_unconfirmed_zone_no_candidate(self):
        a=annotation();a['zones'][0]['confirmed']=False;self.assertEqual(candidates(a),[])
    def test_cash_surface_and_door_conditions(self):
        a=annotation();a['objects'][0]['label']='table';a['states'][0]['value']='cash_present'
        self.assertEqual(candidates(a)[0]['label'],'cash_unattended')
        a['objects'][0]['label']='room_door';a['states'][0]['value']='open'
        self.assertEqual(candidates(a),[])
        a['thresholds']['door']=10;self.assertEqual(candidates(a)[0]['label'],'door_unattended')
    def test_absence_requires_visible(self):
        a=annotation();a['states'][1]['fully_visible']=False
        with self.assertRaises(ValueError):Annotation.model_validate(a)
    def test_role_and_references(self):
        a=annotation();a['events']=[{'id':'e','label':'other','start':0,'end':1,'actor_id':'o'}]
        with self.assertRaises(ValueError):Annotation.model_validate(a)

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=make_fixture(Path(cls.tmp.name)/'dataset')
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):
        self.runtime=tempfile.TemporaryDirectory();self.client=TestClient(create_app(self.root,self.runtime.name));self.vid=self.client.get('/api/videos').json()[0]['id']
    def tearDown(self):self.client.close();self.runtime.cleanup()
    def save(self,a,revision=0):return self.client.put(f'/api/videos/{self.vid}/annotation',json={'revision':revision,'annotation':a})
    def test_sources_unchanged_and_no_automatic_human_labels(self):
        before=(self.root/'reference/labels_final.json').read_bytes();d=self.client.get('/api/videos/'+self.vid).json();self.assertEqual(d['annotation']['events'],[]);self.assertEqual(d['revision'],0)
        self.assertEqual(self.save(annotation()).status_code,200);self.assertEqual(before,(self.root/'reference/labels_final.json').read_bytes())
    def test_revision_conflict_and_history(self):
        a=annotation();self.assertEqual(self.save(a).json()['revision'],1);self.assertEqual(self.save(a).status_code,409)
        a['notes']='second';self.assertEqual(self.save(a,1).json()['revision'],2)
        c=sqlite3.connect(Path(self.runtime.name)/'annotations.sqlite3');self.assertEqual(c.execute('SELECT COUNT(*) FROM history').fetchone()[0],2);c.close()
    def test_invalid_time_box_and_review(self):
        a=annotation();a['states'][0]['end']=999;self.assertEqual(self.save(a).status_code,422)
        a=annotation();a['objects'][0]['keyframes'][0]['box']=[.9,.1,.2,.3];self.assertEqual(self.save(a).status_code,422)
        a=annotation();a['status']='reviewed';self.assertEqual(self.save(a).status_code,422)
    def test_rules_and_exports(self):
        a=annotation();self.save(a);self.assertEqual(len(self.client.get(f'/api/videos/{self.vid}/candidates').json()),1)
        self.assertEqual(len(self.client.get('/api/export/coco').json()['annotations']),0)
        a.update(status='reviewed',reviewer='QA',verdict='uncertain',full_video_reviewed=True);self.assertEqual(self.save(a,1).status_code,200)
        data=self.client.get('/api/export/coco').json();self.assertEqual(data['annotations'][0]['bbox'],[64,36,128,108]);self.assertEqual(data['images'][0]['group_id'],'synthetic')
        self.assertEqual(len(self.client.get('/api/export').json()['videos']),2)
    def test_media_range_frame_and_boundary(self):
        r=self.client.get(f'/api/videos/{self.vid}/media',headers={'Range':'bytes=0-99'});self.assertEqual(r.status_code,206);self.assertEqual(len(r.content),100)
        r=self.client.get(f'/api/videos/{self.vid}/frame?t=8');self.assertEqual(r.status_code,200);self.assertTrue(r.content.startswith(b'\xff\xd8'))
        self.assertEqual(self.client.get('/api/videos/not-a-video').status_code,404)
        self.assertEqual(self.client.put(f'/api/videos/{self.vid}/annotation',json={'revision':0,'annotation':annotation()},headers={'Origin':'https://other.test'}).status_code,403)
        self.assertIn("script-src 'self'",self.client.get('/').headers['content-security-policy'])

    def test_mask_export_preserves_pixels_and_origin(self):
        a=annotation();a['objects'][0]['keyframes'][0].update(polygons=[[[.1,.1],[.3,.1],[.3,.4],[.1,.4]]],mask_source='sam2.1_small')
        self.assertEqual(self.save(a).status_code,200)
        item=self.client.get('/api/export/coco?reviewed_only=false').json()['annotations'][0]
        self.assertEqual(item['segmentation'],[[64,36,192,36,192,144,64,144]])
        self.assertAlmostEqual(item['area'],128*108);self.assertEqual(item['mask_source'],'sam2.1_small')

if __name__=='__main__':unittest.main()
