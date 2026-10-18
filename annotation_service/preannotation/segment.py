"""SAM2.1 proposal contours. No semantic-quality claim or automatic human acceptance."""
import argparse,json,os,sys,time
from pathlib import Path
from annotation_service.paths import dataset_path, runtime_path, source_records
import cv2,numpy as np
from PIL import Image
from annotation_service.drafts import DraftStore
from .common import write_json
ROOT=Path(__file__).resolve().parents[2]
STATIC={'safe','safe_door','room_door','cash_drawer','table','transfer_tray','chair','money_counter','banknote_detector'}

def polygons(mask,max_components=8):
    h,w=mask.shape;contours,_=cv2.findContours(mask.astype('uint8'),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE);out=[]
    for contour in sorted(contours,key=cv2.contourArea,reverse=True)[:max_components]:
        if cv2.contourArea(contour)<max(9,h*w*.0002):continue
        approx=cv2.approxPolyDP(contour,max(1,cv2.arcLength(contour,True)*.003),True).reshape(-1,2)
        if len(approx)>=3:out.append([[round(float(x)/w,6),round(float(y)/h,6)] for x,y in approx])
    return out

def geometry(mask,t):
    y,x=np.where(mask)
    if not len(x):return None
    h,w=mask.shape;ps=polygons(mask)
    if not ps:return None
    return {'time':t,'box':[float(x.min())/w,float(y.min())/h,float(x.max()-x.min()+1)/w,float(y.max()-y.min()+1)/h],'polygons':ps,'mask_source':'sam2.1_small'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--watch',action='store_true');p.add_argument('--pilot-count',type=int,default=5);p.add_argument('--max-wait',type=int,default=10800);args=p.parse_args()
    import torch
    from sam2.build_sam import build_sam2,build_sam2_video_predictor
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    torch.manual_seed(42);torch.set_num_threads(6)
    config='configs/sam2.1/sam2.1_hiera_s.yaml';checkpoint=os.environ.get('ANNOTATION_SAM_CHECKPOINT',str(ROOT/'backend/models/sam2.1_hiera_small.pt'))
    image_predictor=SAM2ImagePredictor(build_sam2(config,checkpoint,apply_postprocessing=False))
    video_predictor=build_sam2_video_predictor(config,checkpoint,apply_postprocessing=False)
    run=Path(args.run).resolve();store=DraftStore(runtime_path(ROOT)/'annotations.sqlite3');processed=set();pilot_ids=[];began=time.monotonic()
    previous={}
    if (run/'segmentation_report.json').exists():
        previous=json.loads((run/'segmentation_report.json').read_text());processed={v for v,s in previous.get('videos',{}).items() if s.get('status')=='done'};pilot_ids=[v for v in previous.get('pilot_ids',[]) if v in processed]
    report={'model':'SAM2.1 small','source_commit':'2b90b9f5ceec907a1c18123530e92e794ad901a4','pilot_ids':pilot_ids,'processed_ids':sorted(processed),'videos':previous.get('videos',{}),'limitations':['No human masks available: semantic quality and correction-time improvement have not been measured.','Contours omit holes; low-area components are filtered.','Video propagation is model inference, not confirmed object identity.']}
    while True:
        manifests=[p for p in run.glob('*/manifest.json') if json.loads(p.read_text()).get('status')=='done']
        for mp in manifests:
            vid=mp.parent.name
            if vid in processed:continue
            home=mp.parent;draft=json.loads((home/'draft.json').read_text());media=json.loads((home/'frames/frames.json').read_text());frames=media['frames'];objects=[o for o in draft['annotation']['objects'] if o['label'] in STATIC]
            pilot=len(pilot_ids)<args.pilot_count and bool(objects);summary={'video_id':vid,'mode':'video_propagation' if pilot else 'keyframe_masks','objects':len(objects),'mask_keyframes':0,'empty_masks':0,'errors':[]}
            if pilot:pilot_ids.append(vid)
            try:
                with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
                    if pilot:
                        # init_state requires a JPEG-only numeric frame directory.
                        frame_dir=home/'sam_frames';frame_dir.mkdir(exist_ok=True)
                        for f in frames:
                            target=frame_dir/f['file']
                            if not target.exists():target.symlink_to((home/'frames'/f['file']).resolve())
                        state=video_predictor.init_state(str(frame_dir),offload_video_to_cpu=True,offload_state_to_cpu=True)
                        seeds={};outputs={};w,h=media['width'],media['height']
                        for number,o in enumerate(objects):
                            k=o['keyframes'][0];idx=min(range(len(frames)),key=lambda i:abs(frames[i]['time']-k['time']));x,y,bw,bh=k['box'];seeds[number]=o;outputs[number]={}
                            video_predictor.add_new_points_or_box(state,frame_idx=idx,obj_id=number,box=np.array([x*w,y*h,(x+bw)*w,(y+bh)*h],dtype='float32'))
                        for reverse in (False,True):
                            for fi,ids,logits in video_predictor.propagate_in_video(state,reverse=reverse):
                                masks=(logits>0).cpu().numpy()[:,0]
                                for number,mask in zip(ids,masks):
                                    if not mask.any():summary['empty_masks']+=1;continue
                                    if fi%4!=0 and fi not in (0,len(frames)-1):continue
                                    g=geometry(mask,frames[fi]['time'])
                                    if g:outputs[number][g['time']]=g
                        for number,o in seeds.items():
                            keys=sorted(outputs[number].values(),key=lambda k:k['time'])
                            if keys:o['keyframes']=keys;summary['mask_keyframes']+=len(keys)
                            draft['metadata'].setdefault(o['id'],{})['segmentation']={'model':'sam2.1_small','mode':'video_propagation','keyframes':len(keys),'human_verified':False}
                        del state
                    else:
                        for o in objects:
                            for k in o['keyframes']:
                                f=min(frames,key=lambda f:abs(f['time']-k['time']))
                                with Image.open(home/'frames'/f['file']) as im:array=np.array(im.convert('RGB'),copy=True)
                                image_predictor.set_image(array);h,w=array.shape[:2];x,y,bw,bh=k['box'];masks,scores,_=image_predictor.predict(box=np.array([x*w,y*h,(x+bw)*w,(y+bh)*h]),multimask_output=False)
                                g=geometry(masks[0],k['time'])
                                if g:k.update(g);summary['mask_keyframes']+=1
                                else:summary['empty_masks']+=1
                                draft['metadata'].setdefault(o['id'],{})['segmentation']={'model':'sam2.1_small','mode':'keyframe_mask','sam_score':float(scores[0]),'human_verified':False}
                draft['diagnostics']['segmentation']=summary;write_json(home/'draft.json',draft);store.publish(vid,draft);summary['status']='done'
            except Exception as exc:
                summary['status']='failed';summary['errors'].append(str(exc));write_json(home/'segmentation_error.json',summary)
            write_json(home/'segmentation.json',summary);report['videos'][vid]=summary;processed.add(vid);report['processed_ids']=sorted(processed);report['pilot_ids']=pilot_ids;write_json(run/'segmentation_report.json',report);print(json.dumps({k:summary[k] for k in ['video_id','status','mode','mask_keyframes','empty_masks']}),flush=True)
        if not args.watch or len(processed)>=len(source_records(ROOT)) or time.monotonic()-began>args.max_wait:break
        time.sleep(10)
    print('Segmentation worker finished',flush=True)
if __name__=='__main__':main()
