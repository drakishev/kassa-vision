"""Read-only saved geometry, excluding inference narratives and filesystem paths."""
import json
from pathlib import Path
from fastapi import HTTPException


def load_diagnostics(settings, store, job_id):
    result=store.public_job(job_id)
    if result is None: raise HTTPException(404,'Job not found')
    if result['status']!='done': raise HTTPException(409,'Diagnostics require a completed analysis')
    with store.connect() as db:
        row=db.execute('SELECT input_path,attempts FROM jobs WHERE job_id=?',(job_id,)).fetchone()
    root=(settings.runtime/'jobs').resolve()
    directory=Path(row['input_path']).resolve().parent
    run=(directory/f"attempt_{row['attempts']}").resolve()
    path=(run/'structured.json').resolve()
    legacy=False
    if not path.exists():
        path=(run/'evidence.json').resolve()
        legacy=True
    if not directory.is_relative_to(root) or not run.is_relative_to(directory) or not path.is_relative_to(run):
        raise HTTPException(404,'Diagnostics unavailable')
    try: data=json.loads(path.read_text())
    except FileNotFoundError: raise HTTPException(404,'Saved model diagnostics unavailable')
    except (ValueError,OSError): raise HTTPException(422,'Saved model diagnostics unreadable')
    if not isinstance(data,dict) or not isinstance(data.get('frames'),list):
        raise HTTPException(422,'Saved model diagnostics malformed')
    if legacy:
        source=data.get('source',{})
        width,height=source.get('width'),source.get('height')
        if not all(isinstance(x,int) and x>0 for x in (width,height)):
            raise HTTPException(422,'Saved frame dimensions unavailable')
        data['pipeline']='legacy-supported'
        data['coordinates']={'detections':'saved-image pixels xyxy','poses':'saved-image pixels xy with score'}
        for frame in data['frames']:
            frame.update(width=width,height=height)
    return run,data


def pick(data,keys):
    return {k:data[k] for k in keys if k in data} if isinstance(data,dict) else {}


def projection(job_id,data):
    frames=[]
    for index,frame in enumerate(data['frames']):
        out=pick(frame,('frame_id','time_sec','width','height','capacity'))
        out['frame_index']=index
        for key,fields in (
            ('detections',('label','score','box_xyxy','track_id')),
            ('poses',('track_id','keypoints')),
            ('states',('track_id','label','box_xyxy','value','similarity_logits','margin','calibrated','advisory_only'))):
            if key in frame: out[key]=[pick(item,fields) for item in frame[key]]
        frames.append(out)
    rawscene=data.get('scene',{})
    scene=pick(rawscene,('anchor_time_sec','scope'))
    for key,fields in (
        ('objects',('id','label','box')),
        ('masks',('object_id','polygons','source','score','scope')),
        ('zones',('role','polygon'))):
        if key in rawscene: scene[key]=[pick(item,fields) for item in rawscene[key]]
    capabilities={name:('available' if any(key in frame for frame in frames) else 'unavailable')
                  for name,key in [('detection','detections'),('pose','poses'),('states','states')]}
    capabilities['segmentation']='available' if 'masks' in scene else 'unavailable'
    return {'job_id':job_id,'pipeline':data.get('pipeline','unknown'),'frames':frames,'scene':scene,
            'capabilities':capabilities,'coordinates':pick(data.get('coordinates',{}),
            ('detections','poses','scene_boxes','scene_polygons','source_width','source_height'))}


def saved_frame(run,data,index):
    if index<0 or index>=len(data['frames']): raise HTTPException(404,'Frame not found')
    name=data['frames'][index].get('image')
    if not isinstance(name,str): raise HTTPException(404,'Frame unavailable')
    path=(run/name).resolve()
    if not path.is_relative_to(run) or path.suffix.lower() not in {'.jpg','.jpeg'} or not path.is_file():
        raise HTTPException(404,'Frame unavailable')
    return path
