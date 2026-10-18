import json,sqlite3,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from annotation_service.app import create_app
from annotation_service.schema import Annotation
from annotation_service.protocol import protocol_gaps
from annotation_service.tests.test_service import make_fixture


def protocol():
    a=Annotation().model_dump()
    a['participants']=[{'id':'p1','name':'Кассир','role':'cashier'},{'id':'p2','name':'Клиент 1','role':'customer'},{'id':'p3','name':'Клиент 2','role':'customer'}]
    a['episodes']=[
        {'id':'ep1','start':7,'end':8.5,'action':'hand_over','actor_id':'p2','recipient_id':'p1','item':'cash','visibility':'clear'},
        {'id':'ep2','start':20.5,'end':21.5,'action':'hand_over','actor_id':'p3','recipient_id':'p1','item':'document','visibility':'partial','explains_label':True,'hypothesis':'документ клиента 2 до завершения операции клиента 1'}]
    a['label_check']={'status':'partly','text':'работа со вторым клиентом до завершения первой операции'}
    a['reviewer']='проверяющий';a['full_video_reviewed']=True
    return a


class EpisodeSchemaTests(unittest.TestCase):
    def test_complete_protocol_has_no_gaps(self):
        a=Annotation.model_validate(protocol()).model_dump()
        self.assertEqual(protocol_gaps(a,'нарушение'),[])

    def test_rows_saved_before_the_protocol_still_load(self):
        old=Annotation().model_dump()
        for key in ('participants','episodes','label_check'):old.pop(key)
        self.assertEqual(Annotation.model_validate(old).episodes,[])

    def test_references_and_contradictions_are_rejected(self):
        for change in [lambda a:a['episodes'][0].update(actor_id='nobody'),
                       lambda a:a['episodes'][0].update(recipient_id='p2'),
                       lambda a:a['episodes'][1].update(hard_negative=True),
                       lambda a:a['episodes'][0].update(item='banana'),
                       lambda a:a['participants'].append({'id':'ep1','name':'Дубль','role':'other'})]:
            a=protocol();change(a)
            with self.assertRaises(ValueError):Annotation.model_validate(a)

    def test_gaps_explain_what_is_missing(self):
        a=protocol();a['label_check']['status']='unset';a['episodes'][1]['recipient_id']='';a['full_video_reviewed']=False
        gaps=protocol_gaps(a,'нарушение')
        self.assertEqual(len(gaps),3)
        a=protocol();a['episodes'][1]['explains_label']=False
        self.assertIn('Нет эпизода с отметкой «объясняет метку»',protocol_gaps(a,'нарушение'))
        # A normal video needs no label explanation, only recorded ordinary actions.
        a=protocol();a['label_check']={'status':'unset','text':''}
        self.assertEqual(protocol_gaps(a,'без нарушения'),[])


class EpisodeApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.dataset=make_fixture(root/'dataset');self.runtime=root/'runtime'
        self.client=TestClient(create_app(self.dataset,self.runtime))
        self.vid=self.client.get('/api/videos').json()[0]['id']

    def test_episode_round_trip_and_export(self):
        config=self.client.get('/api/config').json()
        self.assertIn('hand_over',config['actions']);self.assertIn('explained',config['label_checks'])
        r=self.client.put(f'/api/videos/{self.vid}/annotation',json={'revision':0,'annotation':protocol()})
        self.assertEqual(r.status_code,200,r.text)
        export=self.client.get('/api/export/episodes').json()
        row=next(x for x in export['videos'] if x['video_id']==self.vid)
        self.assertEqual([e['actor'] for e in row['episodes']],['Клиент 1','Клиент 2'])
        self.assertEqual(row['episodes'][1]['recipient'],'Кассир')
        self.assertNotIn('ai',row)

    def test_episode_after_video_end_is_rejected(self):
        a=protocol();a['episodes'][1]['end']=500
        self.assertEqual(self.client.put(f'/api/videos/{self.vid}/annotation',json={'revision':0,'annotation':a}).status_code,422)

    def test_old_saved_rows_are_served_with_protocol_defaults(self):
        old=Annotation().model_dump()
        for key in ('participants','episodes','label_check'):old.pop(key)
        with sqlite3.connect(self.runtime/'annotations.sqlite3') as c:
            c.execute('INSERT INTO annotations VALUES(?,?,?,?)',(self.vid,1,json.dumps(old),'2026-10-17T00:00:00+00:00'))
        a=self.client.get(f'/api/videos/{self.vid}').json()['annotation']
        self.assertEqual((a['episodes'],a['participants'],a['label_check']['status']),([],[],'unset'))


if __name__=='__main__':unittest.main()
