"""Offline full-clip proposals. Run with the GPU dependencies on a free GPU."""
import argparse,hashlib,json,os,sys,time,traceback
from pathlib import Path
from annotation_service.paths import dataset_path, runtime_path, source_records
import cv2
from PIL import Image
from annotation_service.schema import Annotation,Obj,OBJECTS,ZONES,EVENTS,STATES
from annotation_service.drafts import DraftStore
from .common import write_json,parse_json,windows,video_id,validate_geometry,add_window,iou
ROOT=Path(__file__).resolve().parents[2]
SUPPORT=ROOT/'backend'

def extract(path,folder,fps=2):
    index=folder/'frames.json'
    if index.exists():return json.loads(index.read_text())
    folder.mkdir(parents=True,exist_ok=True);cap=cv2.VideoCapture(str(path));rate=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if not cap.isOpened() or rate<=0:raise ValueError('Cannot decode video')
    out={'duration':count/rate,'fps':rate,'timestamp_source':'decoded_pts','width':int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),'height':int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),'frames':[]};i=0;next_t=0
    while True:
        ok,frame=cap.read()
        if not ok:break
        t=cap.get(cv2.CAP_PROP_POS_MSEC)/1000;i+=1
        if t+1e-6<next_t:continue
        next_t+=1/fps;p=folder/f'{len(out["frames"]):06d}.jpg';cv2.imwrite(str(p),frame,[cv2.IMWRITE_JPEG_QUALITY,93]);out['frames'].append({'time':round(t,4),'file':p.name})
    cap.release()
    if not out['frames'] or out['frames'][-1]['time']<out['duration']-.6:raise ValueError('Incomplete video decoding')
    write_json(index,out);return out

def images_at(path,times):
    # MOV/VFR seeking by average FPS can miss valid tail frames. Decode once,
    # select nearest real PTS, and tell the VLM the actual selected timestamps.
    cap=cv2.VideoCapture(str(path));images=[];actual=[];previous=None;index=0
    while index<len(times):
        ok,bgr=cap.read()
        if not ok:break
        t=cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        while index<len(times) and t>=times[index]:
            chosen=(t,bgr) if previous is None or t-times[index]<=times[index]-previous[0] else previous
            if not actual or abs(chosen[0]-actual[-1])>1e-6:
                actual.append(round(chosen[0],6));images.append(Image.fromarray(cv2.cvtColor(chosen[1],cv2.COLOR_BGR2RGB)))
            index+=1
        previous=(t,bgr)
    cap.release()
    if index<len(times) and previous is not None:
        if times[-1]-previous[0]>.1:raise ValueError('Requested refinement beyond decoded video')
        if not actual or abs(previous[0]-actual[-1])>1e-6:
            actual.append(round(previous[0],6));images.append(Image.fromarray(cv2.cvtColor(previous[1],cv2.COLOR_BGR2RGB)))
    if not images:raise ValueError('No refinement frames decoded')
    return images,actual

def invoke(vlm,images,prompt,folder,tag,max_tokens=2400):
    dest=folder/f'{tag}.json'
    request_file=folder/f'{tag}_request.json'
    request={'prompt':prompt,'image_count':len(images),'max_tokens':max_tokens,
             'image_hashes':[hashlib.sha256(str((im.mode,im.size)).encode()+im.tobytes()).hexdigest() for im in images],
             'backend_identity':getattr(vlm,'cache_identity',type(vlm).__module__+'.'+type(vlm).__qualname__)}
    if dest.exists() and request_file.exists() and json.loads(request_file.read_text())==request:return json.loads(dest.read_text())
    if dest.exists():
        archive=folder/'superseded_requests'/f'{tag}_{time.time_ns()}';archive.mkdir(parents=True)
        for old in folder.glob(f'{tag}*'):
            if old.is_file():old.rename(archive/old.name)
    write_json(folder/f'{tag}_request.json',request)
    failures=[]
    for attempt in range(2):
        try:
            budget=max_tokens if not attempt else int(max_tokens*1.5)
            raw,timing=vlm.generate(images,prompt+('\nОтвет должен быть только корректным JSON.' if attempt else ''),max_new_tokens=budget,deadline=time.monotonic()+240)
            (folder/f'{tag}_{attempt}_raw.txt').write_text(raw)
            parsed=parse_json(raw)
            required=('objects','zones') if tag=='geometry' else ('events','states') if tag.startswith('window_') else ()
            if any(not isinstance(parsed.get(k),list) for k in required):raise ValueError('Missing structured proposal lists')
            if tag.startswith('refine_') and not isinstance(parsed.get('supported'),bool):raise ValueError('Missing refinement support flag')
            timing['max_tokens']=budget;timing['attempt']=attempt;write_json(dest,parsed);write_json(folder/f'{tag}_timing.json',timing);return parsed
        except ValueError as e:failures.append(str(e))
    raise ValueError('Invalid structured output: '+'; '.join(failures))

def process(row,run,vlm,detector,store):
    filename=row['filename'];vid=video_id(filename);prefix='d'+run.name[-8:]+'_'+vid[:7];home=run/vid;home.mkdir(exist_ok=True)
    source=dataset_path(ROOT)/'open/videos'/filename;manifest_file=home/'manifest.json'
    if manifest_file.exists() and json.loads(manifest_file.read_text()).get('status')=='done':return
    manifest={'video_id':vid,'filename':filename,'status':'processing','started_at':time.time()};write_json(manifest_file,manifest)
    media=extract(source,home/'frames');frames=media['frames'];duration=media['duration']
    a=Annotation().model_dump();metadata={};coverage={'duration':duration,'sample_fps':2,'sample_times':[f['time'] for f in frames],'windows':[],'refinements':[],'gaps':[],'interpretation':'Full temporal sampling, not exhaustive event detection or human review.'};diagnostics={'rejected':[],'segmentation':'pending','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    def publish(status='processing'):
        data={'run_id':run.name,'status':status,'annotation':a,'metadata':metadata,'coverage':coverage,'diagnostics':diagnostics};write_json(home/'draft.json',data);store.publish(vid,data)
    publish()
    detections_file=home/'detections.json'
    if detections_file.exists():detected=json.loads(detections_file.read_text())
    else:
        detector.reset();detected=[]
        for i,f in enumerate(frames):
            with Image.open(home/'frames'/f['file']) as im:items=detector.detect(im.convert('RGB'))
            detected.append({**f,'detections':items})
            if i%40==0:write_json(home/'progress.json',{'phase':'detector','completed':i+1,'total':len(frames)})
        write_json(detections_file,detected)
    tracks={}
    for f in detected:
        for i,x in enumerate(f['detections']):
            tid=x['track_id'] or f"{x['label']}_single_{int(f['time']*1000)}_{i}"
            tracks.setdefault(tid,{'label':x['label'],'keys':[],'scores':[]});tracks[tid]['keys'].append({'time':f['time'],'box':x['box']});tracks[tid]['scores'].append(x['score'])
    # People and moving small objects get measured anonymous tracks, never roles.
    for i,(tid,track) in enumerate(tracks.items()):
        if track['label'] not in ('person','cash','document','bag','phone'):continue
        if len(track['keys'])<2:continue
        oid=f'{prefix}_t{i}';o=Obj(id=oid,label=track['label'],name=OBJECTS[track['label']],keyframes=track['keys'],interpolate=False);a['objects'].append(o.model_dump());metadata[oid]={'source':'grounding_dino_bytetrack','track_id':tid,'mean_detector_score':sum(track['scores'])/len(track['scores']),'evidence_times':[track['keys'][0]['time'],track['keys'][len(track['keys'])//2]['time'],track['keys'][-1]['time']],'uncertainty':'Класс и непрерывность трека требуют проверки; отсутствие рамки не означает отсутствие объекта.'}
    chosen=[frames[0],frames[len(frames)//2],frames[-1]];anchor=chosen[1]['time']
    imgs=[]
    for f in chosen:
        with Image.open(home/'frames'/f['file']) as im:imgs.append(im.convert('RGB'))
    prompt=('Ты размечаешь наблюдаемые объекты и зоны банковской кассы, не нарушения. Три кадра одного видео, время '+str([f['time'] for f in chosen])+'. Геометрия ТОЛЬКО среднего кадра. Не более 12 объектов, не более 5 зон. Не рисуй людей, ключи и неразличимые предметы. '
      'Объекты: '+json.dumps({k:v for k,v in OBJECTS.items() if k not in ('person','key')},ensure_ascii=False)+'. Зоны: '+json.dumps(ZONES,ensure_ascii=False)+'. '
      'Координаты [0,1000], box=[left,top,right,bottom], points=[[x,y],...]. Различай корпус сейфа и его дверцу, дверь помещения и мебель. У зоны нет гарантированной связи с ролью человека. Если камера изменилась, camera_changed=true; зоны не предлагай. '
      'Верни JSON {"camera_changed":false,"objects":[{"label":"safe","box":[10,20,100,200],"state":"open|closed|unknown","description":"видимый предмет","uncertainty":"сомнения"}],"zones":[{"label":"cashier","points":[[0,0],[100,0],[100,100]]}]}. Не заполняй классы, которые не видны.')
    geometry=invoke(vlm,imgs,prompt,home,'geometry',2400);ga,gm,rejected=validate_geometry(geometry,anchor,prefix);a['objects'].extend(ga['objects']);a['states'].extend(ga['states']);metadata.update(gm);diagnostics['rejected'].extend(rejected)
    if geometry.get('camera_changed') is True:diagnostics['camera_changed']=True;diagnostics['zone_limitation']='Camera change suspected; automatic zones suppressed.'
    else:a['zones']=ga['zones']
    for o in a['objects']:
        if o['label'] not in ('safe','safe_door','cash_drawer','room_door','table','chair','transfer_tray','money_counter','banknote_detector'):continue
        box=o['keyframes'][0]['box'];cx,cy=box[0]+box[2]/2,box[1]+box[3]/2
        for z in a['zones']:
            contour=__import__('numpy').asarray(z['points'],dtype='float32')
            if cv2.pointPolygonTest(contour,(cx,cy),False)>=0:
                o['zone_id']=z['id'];z['anchors'].append(o['id']);break
    publish()
    references={'objects':[{'id':o['id'],'label':o['label'],'box_at_time':o['keyframes'][0]} for o in a['objects']],'zones':[{'id':z['id'],'label':z['label'],'points':z['points']} for z in a['zones']]}
    allowed_events={k:v for k,v in EVENTS.items() if not k.endswith('_unattended')};ref_json=json.dumps(references,ensure_ascii=False)
    for wi,(start,end) in enumerate(windows(duration)):
        fs=[f for f in frames if start-1e-5<=f['time']<=end+1e-5][::2];times=[f['time'] for f in fs];imgs=[]
        for f in fs:
            with Image.open(home/'frames'/f['file']) as im:imgs.append(im.convert('RGB'))
        prompt=('Найди непосредственно видимые действия и состояния на последовательности кадров. Полностью локальная черновая разметка; никаких правовых выводов, личностей, намерений, сумм или невидимых действий. '
          'Времена кадров в секундах: '+json.dumps(times)+'. Анализируемый интервал ['+str(start)+','+str(end)+']. '
          'Предположительные объекты и зоны (ошибки возможны): '+ref_json+'. Ссылки только на перечисленные ID; если связь неясна, пустая строка. '
          'События: '+json.dumps(allowed_events,ensure_ascii=False)+'. Состояния: '+json.dumps(STATES,ensure_ascii=False)+'. '
          'person_present/person_absent относятся к ЗОНЕ, open/closed к сейфу, дверце, денежному ящику или двери. Отсутствие отмечай только если вся зона видна; пропуск детекции не доказывает отсутствие. Закрыто не значит заперто. '
          'Передача денег: нужны видимые деньги, удерживание A до передачи, отпускание/получение и удерживание B после. Пересечение рук или перенос между собственными руками недостаточны. Передача через лоток = положил A, затем забрал B. Неуверенное действие обозначай other с пояснением. '
          'Не более 8 событий и 12 состояний, пустые списки допустимы. JSON: {"events":[{"label":"cash_transfer","start":0,"end":1,"actor_id":"","target_id":"","zone_id":"","note":"наблюдение","evidence_times":[0,0.5,1],"uncertainty":"","transfer":{"cash_visible":true,"release_receive_visible":true,"holder_before":"ID или описание A","holder_after":"ID или описание B","before":"что видно до","interaction":"что видно при взаимодействии","after":"что видно после"}}],"states":[{"target_id":"ID","value":"open","start":0,"end":1,"fully_visible":false,"note":"что видно","evidence_times":[0,1]}]}. Все времена в исходных секундах внутри анализируемого интервала.')
        tick=time.monotonic();raw=invoke(vlm,imgs,prompt,home,f'window_{wi:03d}',2600)
        diagnostics['rejected'].extend(add_window(raw,a,metadata,start,end,prefix,wi,times));coverage['windows'].append({'start':start,'end':end,'frame_times':times,'status':'done','elapsed_sec':round(time.monotonic()-tick,2)});publish();write_json(home/'progress.json',{'phase':'vlm_windows','completed':wi+1,'total':len(list(windows(duration)))})
    # Candidate-boundary refinement is explicit and cached; no human label is used.
    for ei,event in enumerate(list(a['events'])):
        start=max(0,event['start']-2);end=min(duration-1/media['fps'],event['end']+2)
        if end<start:continue
        # Use two tight boundary neighborhoods, not a sparse sample of the whole event.
        times=sorted(set(round(t,3) for anchor_t in [event['start'],event['end']] for j in range(17) for t in [anchor_t-2+j/4] if 0<=t<duration-1/media['fps']))
        if not times:continue
        imgs,times=images_at(source,times)
        prompt=('Уточни один предполагаемый эпизод по плотным кадрам возле его границ. Кадры идут в порядке времени '+json.dumps(times)+'. Исходное предложение '+json.dumps(event,ensure_ascii=False)+'. '
          'Не подтверждай автоматически. Типы: '+json.dumps(allowed_events,ensure_ascii=False)+'. Верни JSON {"supported":true,"label":"other","start":0,"end":1,"note":"непосредственно наблюдаемое","uncertainty":"что не видно","evidence_times":[0,0.5,1],"transfer":{"cash_visible":false,"release_receive_visible":false,"holder_before":"","holder_after":"","before":"","interaction":"","after":""}}. '
          'Передача денег требует наблюдения денег, отпускания/получения и разных удерживающих до и после. Если этого не видно, supported=false или label=other. Времена внутри ['+str(start)+','+str(end)+']. Если уточнение границы невозможно, сохрани исходную и поясни неопределенность.')
        raw=invoke(vlm,imgs,prompt,home,f'refine_{ei:03d}',1200);md=metadata[event['id']];md['refinement']=raw;md['refinement_frame_times']=times
        if raw.get('supported') is False:md['uncertainty']='Уточняющий проход не подтвердил исходную гипотезу. '+str(raw.get('uncertainty',''));event['label']='other';event['note']='Не подтверждено уточняющим проходом: '+event['note']
        else:
            try:
                s,e=float(raw['start']),float(raw['end'])
                if not start<=s<=e<=end:raise ValueError('Refinement outside observed times')
                event['start']=s;event['end']=e;event['note']=str(raw.get('note',event['note']))[:3500]
                if raw.get('label') in allowed_events:event['label']=raw['label']
                if event['label']=='cash_transfer':
                    from .common import transfer_assessment
                    md['transfer']=raw.get('transfer',{});md['transfer_support']=transfer_assessment(raw)
                    if md['transfer_support']!='three_phase_hypothesis':event['label']='other';event['note']='Возможная передача предмета; нужны подтверждения денег и смены удерживающего. '+event['note']
                md['evidence_times']=[min(times,key=lambda t:abs(t-v)) for v in [s,(s+e)/2,e]];md['uncertainty']=str(raw.get('uncertainty','Границы уточнены моделью; требуется проверка.'))
            except (ValueError,KeyError,TypeError) as exc:md['refinement_error']=str(exc)
        coverage['refinements'].append({'event_id':event['id'],'frame_times':times});publish()
    # Report earlier anchor coverage, without feeding organizer labels or old text to the model.
    old_file=dataset_path(ROOT)/'audit/labels_annotated.json'
    old={r['filename']:r for r in json.loads(old_file.read_text()) if 'filename' in r}.get(filename,{}) if old_file.exists() else {}
    try:anchor_old=float(old.get('timecode_sec'));diagnostics['previous_ai_anchor_covered']=any(w['start']<=anchor_old<=w['end'] for w in coverage['windows'])
    except (ValueError,TypeError):pass
    diagnostics['review_required']=True;diagnostics['segmentation']='awaiting_pilot';publish('done')
    manifest.update(status='done',finished_at=time.time(),duration=duration,counts={k:len(a[k]) for k in ('objects','zones','states','events')},window_count=len(coverage['windows']));write_json(manifest_file,manifest);print(json.dumps({'video_id':vid,'status':'done','counts':manifest['counts']},ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--shard',type=int,default=0);p.add_argument('--shards',type=int,default=1);p.add_argument('--limit',type=int,default=0);p.add_argument('--vlm-url');args=p.parse_args()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['TOKENIZERS_PARALLELISM']='false'
    import torch
    torch.set_num_threads(6);torch.manual_seed(42)
    if torch.cuda.mem_get_info()[0]<(5 if args.vlm_url else 80)*1024**3:raise RuntimeError('Need >=80GiB free; will not evict other workloads')
    x=torch.randn(32,32,device='cuda');assert torch.isfinite(x@x).all();print('CUDA witness passed',flush=True)
    sys.path.insert(0,str(SUPPORT));from vlm_local import LocalVLM
    from .detector import Detector
    run=Path(args.run).resolve();run.mkdir(parents=True,exist_ok=True)
    store=DraftStore(runtime_path(ROOT)/'annotations.sqlite3')
    rows=source_records(ROOT);rows=rows[args.shard::args.shards]
    if args.limit:rows=rows[:args.limit]
    # Only names are passed onward; original labels are never model inputs.
    rows=[{'filename':r['filename']} for r in rows]
    if args.vlm_url:
        from .vlm_client import LocalVLMClient
        vlm=LocalVLMClient(args.vlm_url)
    else:vlm=LocalVLM()
    detector=Detector(Path(os.environ.get('KASSA_MODELS_DIR',SUPPORT/'models'))/'grounding-dino-base');print('Models ready',flush=True)
    for row in rows:
        try:process(row,run,vlm,detector,store)
        except Exception as exc:
            home=run/video_id(row['filename']);home.mkdir(exist_ok=True);(home/'error.txt').write_text(traceback.format_exc());
            if (home/'draft.json').exists():
                failed=json.loads((home/'draft.json').read_text());failed['status']='failed';failed['coverage']['gaps'].append({'reason':str(exc)});write_json(home/'draft.json',failed);store.publish(video_id(row['filename']),failed)
            write_json(home/'manifest.json',{'video_id':video_id(row['filename']),'filename':row['filename'],'status':'failed','error':str(exc)});print(json.dumps({'video_id':video_id(row['filename']),'status':'failed','error':str(exc)[:250]}),flush=True)
    print('Worker finished',flush=True)
if __name__=='__main__':main()
