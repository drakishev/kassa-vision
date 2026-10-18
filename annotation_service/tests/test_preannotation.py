import unittest,tempfile,subprocess
from pathlib import Path
from annotation_service.preannotation.common import windows,parse_json,validate_geometry,add_window,transfer_assessment
from annotation_service.schema import Annotation
class PreannotationTests(unittest.TestCase):
 def test_windows_cover_full_duration(self):
  for duration in [1,12,25.934,145]:
   w=list(windows(duration));self.assertEqual(w[0][0],0);self.assertEqual(w[-1][1],duration)
   self.assertTrue(all(w[i][0]<=w[i-1][1] for i in range(1,len(w))))
 def test_geometry_excludes_keys_and_marks_zones_unconfirmed(self):
  a,m,r=validate_geometry({'objects':[{'label':'key','box':[0,0,10,10]},{'label':'safe','box':[10,10,200,400],'state':'open'}],'zones':[{'label':'cashier','points':[[0,0],[400,0],[400,500]]}]},1,'x')
  self.assertEqual(len(a['objects']),1);self.assertFalse(a['zones'][0]['confirmed']);Annotation.model_validate(a)
 def test_transfer_needs_phases_and_distinct_holders(self):
  self.assertEqual(transfer_assessment({'transfer':{'cash_visible':True}}),'insufficient_evidence')
  p={'cash_visible':True,'release_receive_visible':True,'before':'cash A','interaction':'release','after':'cash B','holder_before':'A','holder_after':'B'}
  self.assertEqual(transfer_assessment({'transfer':p}),'three_phase_hypothesis');p['holder_after']='A';self.assertEqual(transfer_assessment({'transfer':p}),'insufficient_evidence')
 def test_bad_intervals_and_uncertain_transfer(self):
  a=Annotation().model_dump();m={};raw={'events':[{'label':'cash_transfer','start':1,'end':3,'note':'hands close'},{'label':'other','start':-1,'end':5}]}
  rejected=add_window(raw,a,m,0,5,'x',0,[0,1,2,3,4]);self.assertEqual(a['events'][0]['label'],'other');self.assertEqual(len(rejected),1)
 def test_variable_frame_timestamps_and_tail(self):
  from annotation_service.preannotation.worker import extract,images_at
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);video=root/'vfr.mp4'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=64x64:r=10:d=1','-vf',"select=not(eq(n\\,5))",'-vsync','vfr','-c:v','libx264',str(video)],check=True)
   media=extract(video,root/'frames');self.assertEqual(media['timestamp_source'],'decoded_pts');self.assertAlmostEqual(media['frames'][1]['time'],.6,places=3)
   images,times=images_at(video,[.5,.89]);self.assertEqual(len(images),2);self.assertAlmostEqual(times[-1],.9,places=3)
 def test_cross_zone_and_unidentified_events_stay_distinct(self):
  from annotation_service.tests.test_service import annotation
  import copy
  a=annotation();a['zones'].append({**copy.deepcopy(a['zones'][0]),'id':'z2'});m={}
  events=[{'label':'other','start':1.2,'end':3.8,'zone_id':z,'target_id':'o'} for z in ['z','z2']]
  add_window({'events':events},a,m,0,5,'x',0,[0,1,2,3,4]);self.assertEqual(len(a['events']),2)
  self.assertEqual(m['x_e0_0']['evidence_times'],[1,2,4])
  unknown={'label':'other','start':1,'end':3}
  add_window({'events':[unknown,unknown]},a,m,0,5,'x',1,[0,1,2,3,4]);self.assertEqual(len(a['events']),4)
 def test_audit_preservation_is_required(self):
  import json,sqlite3
  from unittest.mock import patch
  from annotation_service.preannotation.audit import audit
  from annotation_service.preannotation.common import video_id,write_json
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);run=root/'run';home=run/video_id('synthetic.mp4')
   write_json(root/'data/dataset/reference/source_labels.json',[{'filename':'synthetic.mp4'}])
   write_json(home/'manifest.json',{'status':'done'})
   source=root/'data/dataset/open/videos/synthetic.mp4';source.parent.mkdir(parents=True)
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=64x64:r=10:d=1','-c:v','libx264',str(source)],check=True)
   from annotation_service.preannotation.worker import extract
   media=extract(source,home/'frames');times=[f['time'] for f in media['frames']]
   write_json(home/'draft.json',{'annotation':Annotation().model_dump(),'coverage':{'duration':1,'windows':[{'start':0,'end':1,'status':'done','frame_times':times}],'gaps':[],'sample_times':times,'refinements':[]},'metadata':{},'diagnostics':{}})
   write_json(home/'segmentation.json',{'status':'done'})
   runtime=root/'data/annotation_service';runtime.mkdir()
   with sqlite3.connect(runtime/'annotations.sqlite3') as c:
    c.execute('CREATE TABLE annotations(video_id TEXT,revision INTEGER,body TEXT,updated_at TEXT)');c.execute("INSERT INTO annotations VALUES('saved',1,'{}','stamp')")
   with patch('annotation_service.preannotation.audit.ROOT',root), patch.dict('os.environ',{'ANNOTATION_DATASET':str(root/'data/dataset'),'ANNOTATION_RUNTIME':str(runtime)}):
    self.assertTrue(any('snapshot' in i['reason'] for i in audit(run)['issues']))
    with sqlite3.connect(runtime/'annotations.sqlite3') as source,sqlite3.connect(run/'before.sqlite3') as dest:source.backup(dest)
    self.assertEqual(audit(run)['issues'],[])
    with sqlite3.connect(runtime/'annotations.sqlite3') as c:c.execute('UPDATE annotations SET revision=2')
    self.assertTrue(any('annotations changed' in i['reason'] for i in audit(run)['issues']))
if __name__=='__main__':unittest.main()
