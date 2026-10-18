"""Measured boxes and anonymous within-video tracks; no absence inference."""
from pathlib import Path
import numpy as np
PROMPTS={'person':'person','safe':'safe','door':'room_door','table':'table','chair':'chair','tray':'transfer_tray','banknotes':'cash','banknote counter':'money_counter','banknote detector':'banknote_detector','bag':'bag','document':'document','phone':'phone'}
class Detector:
 def __init__(self,root):
  import torch,supervision as sv
  from transformers import AutoProcessor,AutoModelForZeroShotObjectDetection
  self.torch=torch;self.sv=sv;self.processor=AutoProcessor.from_pretrained(root,local_files_only=True);self.model=AutoModelForZeroShotObjectDetection.from_pretrained(root,local_files_only=True).to('cuda').eval();self.reset()
 def reset(self):
  # Supervision scales this 30-fps buffer by frame_rate/30: 120 means
  # four seconds (8 sampled frames at 2 fps), not 120 sampled frames.
  self.trackers={name:self.sv.ByteTrack(track_activation_threshold=.25,lost_track_buffer=120,frame_rate=2) for name in PROMPTS.values()}
 def detect(self,image):
  inputs=self.processor(images=image,text='. '.join(PROMPTS)+'.',return_tensors='pt').to('cuda')
  with self.torch.inference_mode():out=self.model(**inputs)
  result=self.processor.post_process_grounded_object_detection(out,inputs.input_ids,threshold=.25,text_threshold=.25,target_sizes=[(image.height,image.width)])[0]
  items=[]
  for box,score,label in zip(result['boxes'],result['scores'],result['text_labels']):
   label=PROMPTS.get(label.strip())
   if not label:continue
   x1,y1,x2,y2=box.tolist();x1=max(0,min(1,x1/image.width));x2=max(0,min(1,x2/image.width));y1=max(0,min(1,y1/image.height));y2=max(0,min(1,y2/image.height))
   if x2<=x1 or y2<=y1:continue
   items.append({'label':label,'score':round(float(score),4),'box':[x1,y1,x2-x1,y2-y1],'track_id':None})
  for label,tracker in self.trackers.items():
   indices=[i for i,x in enumerate(items) if x['label']==label]
   det=self.sv.Detections.empty()
   if indices:
    boxes=[items[i]['box'] for i in indices]
    det=self.sv.Detections(xyxy=np.asarray([[x,y,x+w,y+h] for x,y,w,h in boxes]),confidence=np.asarray([items[i]['score'] for i in indices]),data={'source_index':np.asarray(indices)})
   tracks=tracker.update_with_detections(det)
   for i,tid in zip(tracks.data.get('source_index',[]),tracks.tracker_id):items[int(i)]['track_id']=f'{label}_{int(tid)}'
  return items
