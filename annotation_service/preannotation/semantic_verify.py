"""Local second-architecture checks. Outputs remain unverified pseudo-label evidence."""
import argparse
import base64
import hashlib
import io
import json
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from annotation_service.schema import EVENTS
from .common import parse_json, windows, write_json
from .verify import digest

VERSION = 'semantic-v2'
COLORS = ['#ff5050', '#20eeee', '#ffee30', '#f070ff', '#40ff70', '#ff9630']
STATUSES = {'supported', 'contradicted', 'uncertain'}


def choose(seq, limit):
    if len(seq) <= limit:
        return seq
    return [seq[round(i*(len(seq)-1)/(limit-1))] for i in range(limit)]


def batches(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i+size]


def plan(home):
    draft = json.loads((home/'draft.json').read_text())
    frames = json.loads((home/'frames/frames.json').read_text())['frames']
    a = draft['annotation']
    tasks = []

    def task(kind, items, times):
        # Nearest actually decoded PTS, not requested timestamps presented as actual.
        selected = sorted({min(range(len(frames)), key=lambda j: abs(frames[j]['time']-t)) for t in times})
        selected = choose(selected, 18)
        tasks.append({'id': f'{kind}_{len(tasks):04d}', 'kind': kind, 'items': items,
                      'frames': [frames[i] for i in selected], 'candidate_frame_count': len(set(times))})

    for group in batches(a['objects'], 4):
        items, times = [], []
        for o in group:
            keys = choose(o['keyframes'], 3)
            items.append({k: o[k] for k in ('id', 'label')})
            times.extend(k['time'] for k in keys)
        task('objects', items, times)
    if a['zones']:
        task('zones', [{k: z[k] for k in ('id', 'label')} for z in a['zones']],
             [frames[0]['time'], frames[len(frames)//2]['time'], frames[-1]['time']])
    for kind in ('states', 'events'):
        ordered = sorted(a[kind], key=lambda x: (x['start'], x['end']))
        for group in batches(ordered, 6):
            items = [{k: v for k, v in x.items() if k not in ('note', 'provenance', 'fully_visible')} for x in group]
            start = max(0, min(x['start'] for x in group)-1)
            end = min(frames[-1]['time'], max(x['end'] for x in group)+1)
            times = [f['time'] for f in frames if start <= f['time'] <= end]
            # Include boundaries among sampled temporal evidence; coverage remains explicitly sampled.
            times = sorted(set(choose(times, 12)+[max(0,min(frames[-1]['time'], t)) for x in group for t in (x['start'],x['end'])]))
            task(kind, items, times)
    masks = [{'id': o['id']+'@'+str(k['time']), 'object_id': o['id'], 'label': o['label'],
              'time': k['time']} for o in a['objects'] for k in o['keyframes'] if k.get('polygons')]
    for group in batches(masks, 6):
        task('masks', group, [x['time'] for x in group])
    for start, end in windows(draft['coverage']['duration'], width=12, stride=10):
        task('blind', [], [f['time'] for f in frames if start <= f['time'] <= end][::2])
    return tasks


def draw_task(home, task):
    draft = json.loads((home/'draft.json').read_text())
    a = draft['annotation']
    objects = {o['id']: o for o in a['objects']}
    zones = {z['id']: z for z in a['zones']}
    aliases = {x['id']: f'P{i+1}' for i, x in enumerate(task['items'])}
    refs = set()
    if task['kind'] in ('states', 'events'):
        refs = {v for x in task['items'] for k,v in x.items() if k in ('target_id','actor_id','zone_id') and v}
    overlays = (set(aliases) if task['kind'] in ('objects', 'zones') else refs)
    refnames = {oid: f'R{i+1}' for i,oid in enumerate(sorted(refs))}
    images = []
    for frame in task['frames']:
        with Image.open(home/'frames'/frame['file']) as im:
            im = im.convert('RGB')
        im.thumbnail((1280,960))
        draw = ImageDraw.Draw(im)
        w,h = im.size
        draw.rectangle((0,0,220,23),fill='black')
        draw.text((5,5),f"t={frame['time']:.4f} seconds",fill='white')
        for i, oid in enumerate(sorted(overlays)):
            color = COLORS[i % len(COLORS)]
            label = aliases.get(oid,refnames.get(oid,oid))
            if oid in zones:
                pts = [(p[0]*w,p[1]*h) for p in zones[oid]['points']]
                draw.line(pts+[pts[0]],fill=color,width=3)
                draw.text(pts[0],label,fill=color,stroke_width=1,stroke_fill='black')
            elif oid in objects and objects[oid]['keyframes']:
                keys = objects[oid]['keyframes']
                key = min(keys,key=lambda k:abs(k['time']-frame['time']))
                # Moving tracks are shown only near a measured timestamp.
                if objects[oid]['label'] in ('person','cash','phone','document','bag') and abs(key['time']-frame['time']) > .3:
                    continue
                if len(keys)>1 and not (keys[0]['time']-.3 <= frame['time'] <= keys[-1]['time']+.3):
                    continue
                x,y,bw,bh = key['box']
                draw.rectangle((x*w,y*h,(x+bw)*w,(y+bh)*h),outline=color,width=3)
                draw.text((x*w,y*h),label,fill=color,stroke_width=1,stroke_fill='black')
        if task['kind']=='masks':
            for i, item in enumerate(task['items']):
                if abs(item['time']-frame['time']) > .01:
                    continue
                key = next(k for k in objects[item['object_id']]['keyframes'] if k['time']==item['time'])
                for p in key['polygons']:
                    pts=[(x*w,y*h) for x,y in p]
                    draw.line(pts+[pts[0]],fill=COLORS[i % len(COLORS)],width=3)
                x,y,_,_=key['box']
                draw.text((x*w,y*h),aliases[item['id']],fill=COLORS[i % len(COLORS)],stroke_width=1,stroke_fill='black')
        images.append(im)
    items=[]
    for x in task['items']:
        item={**x,'id':aliases[x['id']]}
        for k in ('target_id','actor_id','zone_id'):
            if item.get(k):item[k]=refnames[item[k]]
        item.pop('object_id',None)
        items.append(item)
    reference = {name: {'kind':'object' if oid in objects else 'zone',
                        'label':(objects.get(oid) or zones.get(oid))['label']} for oid,name in refnames.items()}
    return images, items, reference, aliases


def prompt_for(task, items, reference):
    times=[f['time'] for f in task['frames']]
    common=("Inspect only visible evidence in these chronological frames of a bank cash desk. "
            "This is anonymized object/action annotation, not employee identification or a violation verdict. "
            "Do not infer cash from hand motion alone, roles from seating alone, absence from occlusion, "
            "or a closed safe from an invisible door. Frame timestamps in seconds: "+json.dumps(times)+". "
            "Copy evidence_times EXACTLY from this timestamp list; they are absolute seconds, NOT frame indices. "
            "If you cannot identify an actual supporting frame, return uncertain with evidence_times=[]. ")
    if task['kind']=='blind':
        return common+('Independently list clearly observed actions, without inventing missing moments. '
          'Allowed labels: '+json.dumps(list(EVENTS))+'. Exclude *_unattended judgments; timers are not established here. '
          'Return JSON only {"events":[{"label":"door_open","start":'+str(times[0])+',"end":'+str(times[-1])+',"evidence_times":'+json.dumps([times[0],times[-1]])+',"reason":"brief visible evidence"}],'
          '"visibility_limited":true}. Times must be within provided frames. Empty list is valid. At most 12 events.')
    return common+('Critically verify candidate '+task['kind']+'. Colored outlines are hypotheses, not ground truth. '
      'P labels identify candidates; R labels identify referenced targets. '
      'For objects check class AND localization AND same object continuity at shown times. '
      'For zones check spatial boundary and plausible function; cashier/customer labels are uncertain unless visually justified. '
      'For masks check whether contour follows the intended object, excluding background/nearby objects. '
      'For states/events assess label, target and timing. A static open door does not prove door_open. '
      'Sampled frames cannot prove continuous absence or exact boundaries. '
      'cash_transfer requires visible cash, distinct people before/after, and visible release/receipt (or set-down/pick-up by different people). '
      'When evidence is insufficient use uncertain, not contradicted. Contradicted means visible evidence contradicts this candidate. '
      'Candidates: '+json.dumps(items)+'. References: '+json.dumps(reference)+'. '
      'Return JSON only {"checks":[{"id":"P1","status":"supported|contradicted|uncertain",'
      '"evidence_times":[],"reason":"at most 20 words",'
      '"cash_visible":false,"release_receive_visible":false,"different_people":false}]}. '
      'Return exactly one check per candidate. The last three booleans matter only for cash_transfer. '
      'Never assume support from prior labels; avoid confidence scores.')


def validate_response(parsed, task, aliases):
    times=[f['time'] for f in task['frames']]
    def evidence(value):
        if not isinstance(value,list) or not value:
            return False
        return all(isinstance(t,(int,float)) and any(abs(t-s)<.06 for s in times) for t in value)
    if task['kind']=='blind':
        rows=parsed.get('events')
        if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows):raise ValueError('Missing blind event list')
        for x in rows:
            if x.get('label') not in EVENTS or x['label'].endswith('_unattended'):
                raise ValueError('Invalid discovery label')
            if not (times[0]-.05 <= float(x['start']) <= float(x['end']) <= times[-1]+.05):
                raise ValueError('Discovery outside observed frame times')
            if not evidence(x.get('evidence_times')):raise ValueError('Unobserved discovery evidence')
        return parsed
    rows=parsed.get('checks')
    if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows):raise ValueError('Missing checks')
    if len(rows)!=len(aliases) or {r.get('id') for r in rows}!=set(aliases.values()):
        raise ValueError('Incomplete or duplicate candidate checks')
    reverse={v:k for k,v in aliases.items()}
    labels={x['id']:x.get('label') for x in task['items']}
    for x in rows:
        if x.get('status') not in STATUSES:raise ValueError('Invalid verification status')
        if x['status'] in ('supported','contradicted') and not evidence(x.get('evidence_times')):
            raise ValueError('Missing/invalid observed evidence for decision')
        x['id']=reverse[x['id']]
        if labels[x['id']]=='cash_transfer' and x['status']=='supported' and not all(x.get(k) is True for k in ('cash_visible','release_receive_visible','different_people')):
            x['status']='uncertain';x['policy_override']='missing_cash_transfer_evidence'
    return parsed


def request(url, images, prompt, budget):
    if urlsplit(url).hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('Only local inference allowed')
    content=[]; hashes=[]
    for im in images:
        stream=io.BytesIO();im.save(stream,format='PNG',compress_level=1);data=stream.getvalue()
        hashes.append(hashlib.sha256(data).hexdigest())
        content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data).decode()}})
    content.append({'type':'text','text':prompt})
    payload={'model':'mage','messages':[{'role':'user','content':content}], 'temperature':0,'max_tokens':budget,'seed':42}
    req=urllib.request.Request(url.rstrip('/')+'/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    start=time.monotonic()
    with urllib.request.urlopen(req,timeout=300) as response:result=json.load(response)
    return result, hashes, time.monotonic()-start


def execute(home, output, task, url):
    dest=output/home.name/'calls'/task['id'];dest.mkdir(parents=True,exist_ok=True)
    images,items,refs,aliases=draw_task(home,task)
    prompt=prompt_for(task,items,refs)
    model_hash=digest(Path('models/mage/config.json'))
    signature={'version':VERSION,'task':task,'prompt':prompt,'input_sha256':digest(home/'draft.json'),
               'frame_sha256':[digest(home/'frames'/f['file']) for f in task['frames']],
               'model_config_sha256':model_hash,'model':'mage','max_tokens':2400}
    key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    result_path=dest/'result.json'
    if result_path.exists():
        old=json.loads(result_path.read_text())
        if old.get('request_hash')==key and old.get('status') in ('done','failed'):return old
    request_path=dest/'request.json'
    if request_path.exists() and json.loads(request_path.read_text())!=signature:
        archive=dest/'superseded'/str(time.time_ns());archive.mkdir(parents=True)
        for old in dest.iterdir():
            if old.is_file():old.rename(archive/old.name)
    write_json(dest/'request.json',signature)
    failures=[]
    for attempt in range(2):
        try:
            result,hashes,elapsed=request(url,images,prompt,2400+attempt*1200)
            write_json(dest/f'raw_{attempt}.json',result)
            choice=result['choices'][0]
            if choice.get('finish_reason')=='length':raise ValueError('Truncated output')
            answer=validate_response(parse_json(choice['message']['content']),task,aliases)
            report={'status':'done','request_hash':key,'task_id':task['id'],'kind':task['kind'],
                    'result':answer,'frame_times':[f['time'] for f in task['frames']],
                    'image_hashes':hashes,'elapsed_sec':elapsed,'attempt':attempt,
                    'evaluation_type':'synthetic_proxy','model':'mage'}
            write_json(result_path,report);return report
        except Exception as exc:
            failures.append({'type':type(exc).__name__,'message':str(exc)[:200]})
    report={'status':'failed','request_hash':key,'task_id':task['id'],'kind':task['kind'],'errors':failures}
    write_json(result_path,report);return report


def run_shard(source,output,url,shard,shards,limit):
    homes=sorted(p.parent for p in source.glob('*/draft.json'))[shard::shards]
    complete=0
    for home in homes:
        tasks=plan(home)
        write_json(output/home.name/'plan.json',{'input_sha256':digest(home/'draft.json'),'tasks':tasks})
        for task in tasks:
            report=execute(home,output,task,url)
            complete+=1
            print(json.dumps({'video_id':home.name,'task':task['id'],'status':report['status'],'completed':complete}),flush=True)
            if limit and complete>=limit:return


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--url',required=True);p.add_argument('--shard',type=int,default=0);p.add_argument('--shards',type=int,default=1)
    p.add_argument('--limit',type=int,default=0)
    args=p.parse_args();run_shard(args.source,args.output,args.url,args.shard,args.shards,args.limit)


if __name__=='__main__':main()
