import copy,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from annotation_service.app import create_app
from annotation_service.drafts import DraftStore
from annotation_service.tests.test_service import make_fixture,annotation
class DraftTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.tmp=tempfile.TemporaryDirectory();cls.dataset=make_fixture(Path(cls.tmp.name)/'dataset')
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):
  self.runtime=tempfile.TemporaryDirectory();self.client=TestClient(create_app(self.dataset,self.runtime.name));self.id=self.client.get('/api/videos').json()[0]['id'];self.store=DraftStore(Path(self.runtime.name)/'annotations.sqlite3')
  self.draft={'run_id':'test','status':'done','annotation':annotation()};self.store.publish(self.id,self.draft)
 def tearDown(self):self.client.close();self.runtime.cleanup()
 def review(self,ids,**kw):return self.client.post(f'/api/videos/{self.id}/draft/review',json={'revision':0,'draft_revision':1,'action':'accept','ids':ids,'reviewer':'QA',**kw})
 def test_separate_idempotent_publish(self):
  self.assertEqual(self.client.get('/api/videos/'+self.id).json()['revision'],0);self.assertEqual(self.store.publish(self.id,self.draft),1)
  self.assertEqual(self.client.get(f'/api/videos/{self.id}/draft').json()['status'],'done')
  self.assertEqual(self.client.get('/api/export').json()['videos'][0]['annotation']['objects'],[])
 def test_accept_dependencies_and_visibility(self):
  self.assertEqual(self.review(['s2']).status_code,422)
  self.assertEqual(self.review(['s2'],confirm_zones=True).status_code,422)
  self.assertEqual(self.review(['s2'],confirm_zones=True,confirm_visibility=True).status_code,200)
  a=self.client.get('/api/videos/'+self.id).json()['annotation'];self.assertEqual(len(a['objects']),1);self.assertEqual(len(a['zones']),1);self.assertEqual(a['status'],'in_progress');self.assertFalse(a['full_video_reviewed'])
  self.assertEqual(self.review(['s2'],revision=1,confirm_zones=True,confirm_visibility=True).status_code,409)
 def test_reject_preserves_current_and_blocks_dependency(self):
  self.assertEqual(self.review(['o'],action='reject').status_code,200);self.assertEqual(self.client.get('/api/videos/'+self.id).json()['revision'],0)
  self.assertEqual(self.review(['s1'],confirm_zones=True).status_code,409)
 def test_revision_conflict(self):
  self.client.put(f'/api/videos/{self.id}/annotation',json={'revision':0,'annotation':annotation()})
  self.assertEqual(self.review(['s1'],confirm_zones=True).status_code,409)
 def test_corrected_entity_and_existing_preserved(self):
  a=annotation();a['objects']=[];a['zones']=[];a['states']=[];a['notes']='human note'
  self.client.put(f'/api/videos/{self.id}/annotation',json={'revision':0,'annotation':a})
  modified=copy.deepcopy(self.draft['annotation']['objects'][0]);modified['name']='Исправленный сейф'
  self.assertEqual(self.review(['o'],revision=1,confirm_zones=True,overrides={'o':modified}).status_code,200)
  a=self.client.get('/api/videos/'+self.id).json()['annotation'];self.assertEqual(a['notes'],'human note');self.assertEqual(a['objects'][0]['name'],'Исправленный сейф')
 def test_accepted_corrected_zone_does_not_reimport_old_anchors(self):
  extra=copy.deepcopy(self.draft['annotation']['objects'][0]);extra['id']='bad';self.draft['annotation']['objects'].append(extra);self.draft['annotation']['zones'][0]['anchors'].append('bad');self.store.publish(self.id,self.draft)
  zone=copy.deepcopy(self.draft['annotation']['zones'][0]);zone['anchors']=[]
  self.assertEqual(self.review(['z'],draft_revision=2,confirm_zones=True,overrides={'z':zone}).status_code,200)
  self.assertEqual(self.review(['bad'],revision=1,draft_revision=2,action='reject').status_code,200)
  response=self.review(['s1'],revision=1,draft_revision=2)
  self.assertEqual(response.status_code,200,response.text)
  a=self.client.get('/api/videos/'+self.id).json()['annotation'];self.assertEqual([o['id'] for o in a['objects']],['o']);self.assertEqual(a['zones'][0]['anchors'],[])
 def test_republished_content_is_not_misrepresented_as_reviewed(self):
  self.assertEqual(self.review(['o'],confirm_zones=True).status_code,200)
  self.assertFalse(self.store.get(self.id)['reviews']['o']['changed_since_review'])
  self.draft['annotation']['objects'][0]['name']='New model proposal';self.store.publish(self.id,self.draft)
  self.assertTrue(self.store.get(self.id)['reviews']['o']['changed_since_review'])
  a=self.client.get('/api/videos/'+self.id).json()['annotation'];self.assertEqual(a['objects'][0]['name'],'Сейф')
if __name__=='__main__':unittest.main()
