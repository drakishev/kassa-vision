"""Conservative reconciliation; model answers cannot create human ground truth."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
from annotation_service.paths import dataset_path

from annotation_service.drafts import entities
from .common import parse_json, write_json, video_id
from .semantic_verify import plan, STATUSES
from .verify import digest


def visible_times(item, kind, frames, annotation):
    times=[f['time'] for f in frames]
    if kind=='masks':return [t for t in times if abs(t-item['time'])<.01]
    if kind in ('states','events'):
        allowed={t for t in times if item['start']-.05<=t<=item['end']+.05}
        objects={o['id'] for o in annotation.get('objects',[])}
        for key in ('actor_id','target_id'):
            ref=item.get(key)
            if ref in objects:
                allowed.intersection_update(visible_times({'id':ref},'objects',frames,annotation))
        return sorted(allowed)
    if kind=='objects':
        obj=next(o for o in annotation['objects'] if o['id']==item['id'])
        keys=obj['keyframes'];out=[]
        if not keys:return []
        for t in times:
            key=min(keys,key=lambda k:abs(t-k['time']))
            if obj['label'] in ('person','cash','phone','document','bag') and abs(key['time']-t)>.3:continue
            if len(keys)>1 and not keys[0]['time']-.3<=t<=keys[-1]['time']+.3:continue
            out.append(t)
        return out
    return times


def normalize_checks(raw, task, annotation):
    """Demote invalid evidence. Never fill evidence gaps from model labels."""
    rows=raw.get('checks')
    aliases={f'P{i+1}':x for i,x in enumerate(task['items'])}
    if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows) or len(rows)!=len(aliases) or {r.get('id') for r in rows}!=set(aliases):
        raise ValueError('missing_or_duplicate_checks')
    out=[]
    for row in rows:
        item=aliases[row['id']];status=row.get('status')
        if status not in STATUSES:raise ValueError('invalid_status')
        r={'id':item['id'],'model_status':status,'status':status,'evidence_times':[], 'limits':[]}
        times=visible_times(item,task['kind'],task['frames'],annotation)
        evidence=row.get('evidence_times')
        valid=(isinstance(evidence,list) and bool(evidence) and all(
            isinstance(t,(int,float)) and not isinstance(t,bool) and math.isfinite(t)
            and any(abs(t-s)<.06 for s in times) for t in evidence))
        if valid:r['evidence_times']=sorted({min(times,key=lambda s:abs(t-s)) for t in evidence})
        elif status!='uncertain':
            r['status']='uncertain';r['limits'].append('no_valid_candidate_specific_evidence')
        if task['kind']=='states' and item['value'].endswith('_absent'):
            r['status']='uncertain';r['limits'].append('continuous_visibility_not_established')
        if task['kind']=='events' and status=='supported':
            needed=3 if item.get('label')=='cash_transfer' else 2
            if len(r['evidence_times'])<needed:
                r['status']='uncertain';r['limits'].append('insufficient_distinct_event_frames')
            if item.get('label')=='cash_transfer' and not all(row.get(k) is True for k in ('cash_visible','release_receive_visible','different_people')):
                r['status']='uncertain';r['limits'].append('cash_transfer_phases_not_established')
        r['scope']={'objects':'displayed_object_samples','zones':'camera_layout_hypothesis',
                    'states':'sampled_state_points','events':'weak_event_window',
                    'masks':'specific_mask_keyframe'}[task['kind']]
        if task['kind'] in ('states','events'):
            r['limits'].append('exact_boundaries_and_continuity_not_verified')
        out.append(r)
    return out


def read_response(call,task,annotation):
    receipt=call/'result.json';request_path=call/'request.json'
    if not receipt.exists():return None,'call_not_finished'
    result=json.loads(receipt.read_text());signature=json.loads(request_path.read_text())
    key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    if result.get('request_hash')!=key:return None,'response_request_mismatch'
    # Use the last attempted call, never a more favorable earlier answer.
    attempt=result['attempt'] if result['status']=='done' else len(result.get('errors',[]))-1
    path=call/f'raw_{attempt}.json'
    if not path.exists():return None,'no_model_response_for_last_attempt'
    if path.stat().st_mtime_ns<request_path.stat().st_mtime_ns:return None,'stale_raw_response'
    try:
        response=json.loads(path.read_text());choice=response['choices'][0]
        if choice.get('finish_reason')=='length':return None,'truncated_model_response'
        value=parse_json(choice['message']['content'])
        if task['kind']=='blind':
            from .semantic_verify import validate_response
            value=validate_response(value,task,{})
            return {'events':value['events'],'source_response_sha256':digest(path)},None
        return {'checks':normalize_checks(value,task,annotation),'source_response_sha256':digest(path)},None
    except (KeyError,ValueError,TypeError,IndexError):return None,'invalid_structured_model_response'


def summarize(source,output,inventory=None):
    if inventory is None:inventory=dataset_path()/'reference/source_labels.json'
    expected={video_id(r['filename']) for r in json.loads(inventory.read_text())}
    actual={p.parent.name for p in source.glob('*/draft.json')}
    if not expected or expected!=actual:raise ValueError('Draft corpus does not match expected video inventory')
    global_counts=collections.Counter();checks=collections.Counter();task_counts=collections.Counter()
    videos=[];candidate_rows=[]
    for home in sorted(p.parent for p in source.glob('*/draft.json')):
        draft=json.loads((home/'draft.json').read_text());a=draft['annotation'];all_items=entities(a)
        structural=json.loads((output/home.name/'structural.json').read_text())
        if structural['input_sha256']!=digest(home/'draft.json'):raise ValueError('Stale structural audit')
        if structural.get('checker_sha256')!=digest(Path(__file__).with_name('verify.py')):raise ValueError('Stale structural checker policy')
        results={};masks={};discovery=[];tasks=[]
        for task in plan(home):
            call=output/home.name/'calls'/task['id']
            request_path=call/'request.json'
            if request_path.exists():
                signature=json.loads(request_path.read_text())
                if signature['input_sha256']!=digest(home/'draft.json') or signature['task']!=task:
                    raise ValueError('Stale semantic request')
                if signature['frame_sha256']!=[digest(home/'frames'/f['file']) for f in task['frames']]:
                    raise ValueError('Changed sampled image input')
                response,error=read_response(call,task,a)
            else:response,error=None,'not_attempted'
            task_counts['expected']+=1;task_counts['attempted']+=request_path.exists()
            task_counts['usable_response' if not error else error]+=1
            tasks.append({'id':task['id'],'kind':task['kind'],'error':error,
                          'frame_times':[f['time'] for f in task['frames']]})
            if task['kind']=='blind':
                if response:discovery.extend(response['events'])
                continue
            parsed={r['id']:r for r in response['checks']} if response else {}
            for item in task['items']:
                record=parsed.get(item['id'],{'id':item['id'],'status':'error','limits':[error],'evidence_times':[]})
                record.update(task_id=task['id'],kind=task['kind'],
                              response_sha256=response['source_response_sha256'] if response else None)
                (masks if task['kind']=='masks' else results)[item['id']]=record
        if set(results)!=set(all_items):raise ValueError('Proposal coverage mismatch')
        issue_map=collections.defaultdict(set)
        for issue in structural['issues']:
            for oid in issue['ids']:issue_map[oid].add(issue['code'])
        for oid,r in results.items():
            r['structural_flags']=sorted(issue_map[oid]);r['candidate_status']='held'
            if r['status']=='supported' and not issue_map[oid]:r['candidate_status']='model_supported_candidate'
            if r['kind']=='zones':
                r['candidate_status']='held';r['limits'].append('camera_calibration_not_confirmed')
        # Objects may be useful independent of their proposed zone. Intervals need reliable targets.
        for oid,r in results.items():
            if r['candidate_status']!='model_supported_candidate' or r['kind'] not in ('states','events'):continue
            item=all_items[oid][1]
            refs=[item[k] for k in ('target_id','actor_id','zone_id') if item.get(k)]
            if any(results[ref]['candidate_status']!='model_supported_candidate' for ref in refs):
                r['candidate_status']='held';r['limits'].append('unconfirmed_target_or_zone')
        for mid,r in masks.items():
            owner=mid.rsplit('@',1)[0]
            r['structural_flags']=sorted(issue_map[owner]);r['candidate_status']='held'
            if r['status']=='supported' and not issue_map[owner] and results[owner]['status']=='supported':
                r['candidate_status']='model_supported_mask_candidate'
            r['training_ready']=False
        # Discovery overlap is triage only; neither hits nor misses are recall/precision.
        unmatched=[]
        for event in discovery:
            if not any(e['label']==event['label'] and min(e['end'],event['end'])>=max(e['start'],event['start']) for e in a['events']):
                unmatched.append(event)
        for r in results.values():
            checks[r['status']]+=1;global_counts[r['candidate_status']]+=1
            if r['candidate_status']=='model_supported_candidate':
                candidate_rows.append({'video_id':home.name,'proposal_id':r['id'],'kind':r['kind'],
                   'input_sha256':digest(home/'draft.json'),'evidence_times':r['evidence_times'],
                   'scope':r['scope'],'origin':'model_generated_model_checked','training_ready':False,
                   'limits':r['limits']+['frame_annotation_completeness_and_independent_quality_not_established']})
        detail={'video_id':home.name,'input_sha256':digest(home/'draft.json'), 'proposals':results,
                'masks':masks,'tasks':tasks,'blind_observations':discovery,'blind_unmatched_hypotheses':unmatched,
                'human_reviewed':False,'evaluation_type':'synthetic_proxy'}
        write_json(output/home.name/'verification.json',detail)
        videos.append({'video_id':home.name,'proposals':len(results),'mask_checks':len(masks),
                       'statuses':dict(collections.Counter(r['status'] for r in results.values())),
                       'task_errors':sum(t['error'] is not None for t in tasks),
                       'blind_unmatched_hypotheses':len(unmatched)})
    result={'videos':videos,'video_count':len(videos),'proposal_statuses':dict(checks),
            'candidate_counts':dict(global_counts),'task_counts':dict(task_counts),
            'mask_statuses':{},'training_ready':False,
            'interpretation':'Automated checks and filtered weak-label candidates, not verified ground truth or exhaustive recall.'}
    result['all_tasks_finished']=task_counts['expected']==task_counts['attempted'] and not task_counts['call_not_finished']
    result['all_responses_usable']=task_counts['usable_response']==task_counts['expected']
    maskcounts=collections.Counter()
    for row in videos:
        detail=json.loads((output/row['video_id']/'verification.json').read_text())
        maskcounts.update(r['status'] for r in detail['masks'].values())
    result['mask_statuses']=dict(maskcounts)
    write_json(output/'summary.json',result)
    write_json(output/'candidate_manifest.json',{'origin':'automated_proxy','training_ready':False,
       'warning':'Partial positive labels. Do not treat unlabelled objects/frames as background; no random frame split. No independent semantic accuracy estimate.',
       'candidates':candidate_rows})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();r=summarize(args.source,args.output)
    print(json.dumps({k:v for k,v in r.items() if k!='videos'},indent=2))


if __name__=='__main__':main()
