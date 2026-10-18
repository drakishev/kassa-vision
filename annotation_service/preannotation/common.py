import hashlib,json,math
from pathlib import Path
from annotation_service.schema import Annotation,Obj,Zone,State,Event,OBJECTS,ZONES,EVENTS

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(path)
def parse_json(text):
    decoder=json.JSONDecoder()
    for i,c in enumerate(text):
        if c=='{':
            try:
                value,_=decoder.raw_decode(text[i:])
                if isinstance(value,dict):return value
            except ValueError:pass
    raise ValueError('No valid JSON object in model output')
def windows(duration,width=12,stride=9):
    start=0
    while start<duration:
        end=min(duration,start+width);yield (start,end)
        if end>=duration:break
        start+=stride

def video_id(filename):return hashlib.sha256(filename.encode()).hexdigest()[:20]
def bbox(box,scale=1000):
    raw=[float(n)/scale for n in box]
    if not all(math.isfinite(v) for v in raw):raise ValueError('Non-finite box')
    x1,y1,x2,y2=[max(0,min(1,v)) for v in raw]
    if not all(math.isfinite(v) for v in [x1,y1,x2,y2]) or x2-x1<.003 or y2-y1<.003:raise ValueError('Degenerate box')
    return [x1,y1,x2-x1,y2-y1]
def iou(a,b):
    x,y,w,h=a;u,v,p,q=b;intersection=max(0,min(x+w,u+p)-max(x,u))*max(0,min(y+h,v+q)-max(y,v))
    return intersection/max(1e-9,w*h+p*q-intersection)
def validate_geometry(raw,t,prefix):
    a=Annotation().model_dump();metadata={};rejected=[]
    for i,x in enumerate(raw.get('objects',[])):
        try:
            if x['label'] in ('key','person'):continue
            o=Obj(id=f'{prefix}_o{i}',label=x['label'],name=OBJECTS[x['label']],keyframes=[{'time':t,'box':bbox(x['box'])}]);a['objects'].append(o.model_dump())
            metadata[o.id]={'source':'local_vlm','evidence_times':[t],'uncertainty':str(x.get('uncertainty','Границы предложены VLM; требуется проверка.')),'observation':str(x.get('description',''))}
            state=x.get('state','unknown')
            if state in ('open','closed') and o.label in ('safe','safe_door','cash_drawer','room_door'):
                sid=f'{prefix}_gs{i}';a['states'].append(State(id=sid,target_id=o.id,start=t,end=t,value=state,note='Состояние в опорном кадре; длительность не установлена.').model_dump());metadata[sid]={'source':'local_vlm','evidence_times':[t],'uncertainty':'Один кадр, требуется проверка.'}
        except (ValueError,KeyError,TypeError) as e:rejected.append({'kind':'object','index':i,'error':type(e).__name__})
    for i,x in enumerate(raw.get('zones',[])):
        try:
            points=[[float(v)/1000 for v in p] for p in x['points']]
            if not all(math.isfinite(v) for p in points for v in p):raise ValueError('Non-finite zone')
            z=Zone(id=f'{prefix}_z{i}',label=x['label'],name=ZONES[x['label']],points=[[max(0,min(1,v)) for v in p] for p in points],confirmed=False);a['zones'].append(z.model_dump());metadata[z.id]={'source':'local_vlm','evidence_times':[t],'uncertainty':'Предположение по расположению предметов. Роль человека не установлена.'}
        except (ValueError,KeyError,TypeError) as e:rejected.append({'kind':'zone','index':i,'error':type(e).__name__})
    return a,metadata,rejected

def transfer_assessment(x):
    p=x.get('transfer',{})
    supported=(p.get('cash_visible') is True and p.get('release_receive_visible') is True and bool(p.get('before')) and bool(p.get('interaction')) and bool(p.get('after')) and p.get('holder_before') and p.get('holder_after') and p.get('holder_before')!=p.get('holder_after'))
    return 'three_phase_hypothesis' if supported else 'insufficient_evidence'

def add_window(raw,a,metadata,start,end,prefix,index,observed_times):
    objects={o['id']:o for o in a['objects']};zones={z['id']:z for z in a['zones']};targets=set(objects)|set(zones);rejected=[]
    def ref(x):return x if isinstance(x,str) and x in targets else ''
    def interval(x):
        s,e=float(x['start']),float(x['end'])
        if not (math.isfinite(s) and math.isfinite(e) and start-.05<=s<=e<=end+.05):raise ValueError('Interval outside analyzed window')
        return max(start,s),min(end,e)
    def evidence(x):
        times=[]
        for t in x.get('evidence_times',[]):
            try:t=float(t)
            except (ValueError,TypeError):continue
            if observed_times and start-.05<=t<=end+.05:times.append(min(observed_times,key=lambda u:abs(u-t)))
        if not times and observed_times:
            s,e=interval(x);times=[min(observed_times,key=lambda u:abs(u-t)) for t in [s,(s+e)/2,e]]
        return sorted(set(times))[:3]
    for i,x in enumerate(raw.get('events',[])):
        try:
            s,e=interval(x);label=x['label'];note=str(x.get('note',''))[:3500];assessment=None
            if label=='cash_transfer':
                assessment=transfer_assessment(x)
                if assessment!='three_phase_hypothesis':label='other';note='Возможная передача предмета; деньги или смена удерживающего не подтверждены. '+note
            actor=ref(x.get('actor_id'))
            if actor and (actor not in objects or objects[actor]['label']!='person'):actor=''
            ev=Event(id=f'{prefix}_e{index}_{i}',label=label,start=s,end=e,note=note,actor_id=actor,target_id=ref(x.get('target_id')),zone_id=x.get('zone_id') if x.get('zone_id') in zones else '',provenance='ai_suggestion')
            duplicates=[old for old in a['events'] if (ev.actor_id or ev.target_id) and old['label']==ev.label and old['actor_id']==ev.actor_id and old['target_id']==ev.target_id and old['zone_id']==ev.zone_id and min(old['end'],e)-max(old['start'],s)>=.5]
            if duplicates:
                old=duplicates[0];old['start']=min(old['start'],s);old['end']=max(old['end'],e);metadata[old['id']].setdefault('additional_observations',[]).append({'window':[start,end],'evidence_times':evidence(x),'note':note,'uncertainty':x.get('uncertainty',''),'transfer':x.get('transfer',{})});continue
            a['events'].append(ev.model_dump());metadata[ev.id]={'source':'local_vlm','window':[start,end],'evidence_times':evidence(x),'uncertainty':str(x.get('uncertainty','Предложение по выборке кадров; проверить видео.')),'transfer':x.get('transfer',{}),'transfer_support':assessment}
        except (ValueError,KeyError,TypeError) as exc:rejected.append({'kind':'event','window':[start,end],'index':i,'error':str(exc)[:160]})
    for i,x in enumerate(raw.get('states',[])):
        try:
            s,e=interval(x);tid=ref(x.get('target_id'));value=x['value'];visible=x.get('fully_visible') is True
            if not tid:raise ValueError('Unknown state target')
            if value.startswith('person_') and tid not in zones:raise ValueError('Presence requires zone')
            if value in ('open','closed') and (tid not in objects or objects[tid]['label'] not in ('safe','safe_door','cash_drawer','room_door')):raise ValueError('Invalid open/closed target')
            if value.endswith('_absent') and not visible:value='unknown'
            st=State(id=f'{prefix}_s{index}_{i}',start=s,end=e,target_id=tid,value=value,fully_visible=visible,note=str(x.get('note',''))[:3500]);a['states'].append(st.model_dump());metadata[st.id]={'source':'local_vlm','evidence_times':evidence(x),'uncertainty':'Видимость и непрерывность состояния оценены моделью, требуют подтверждения.'}
        except (ValueError,KeyError,TypeError) as exc:rejected.append({'kind':'state','window':[start,end],'index':i,'error':str(exc)[:160]})
    return rejected
