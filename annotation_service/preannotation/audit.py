"""Aggregate local execution evidence, without printing private observations."""
import argparse, collections, json, math, sqlite3
from pathlib import Path
from annotation_service.paths import dataset_path, runtime_path, source_records
from annotation_service.schema import Annotation
from .common import video_id, write_json, windows
ROOT=Path(__file__).resolve().parents[2]

def sampling_issues(home,coverage,source):
    """Check concrete sampling artifacts, not just declared done/window flags."""
    found=[]
    if not source.is_file() or source.stat().st_size==0:found.append('missing source video')
    index=home/'frames/frames.json'
    if not index.exists():return found+['missing decoded frame index']
    try:
        media=json.loads(index.read_text());frames=media['frames'];times=[f['time'] for f in frames]
        duration=coverage['duration'];declared=coverage['sample_times']
        if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:
            return found+['invalid video duration']
        if not times or any(not isinstance(t,(int,float)) or not math.isfinite(t) or t<0 or t>duration+.05 for t in times):
            return found+['empty or invalid decoded timestamps']
        if times!=sorted(set(times)) or times!=declared:found.append('decoded timestamps disagree with coverage')
        if media.get('timestamp_source')!='decoded_pts':found.append('timestamps lack decoded PTS provenance')
        if abs(media['duration']-duration)>.05 or times[0]>.6 or times[-1]<duration-.6:
            found.append('incomplete decoded temporal extent')
        if any(b-a>.7 for a,b in zip(times,times[1:])):found.append('decoded sampling gap exceeds 0.7 seconds')
        if any(Path(f['file']).name!=f['file'] or not (home/'frames'/f['file']).is_file()
               or (home/'frames'/f['file']).stat().st_size==0 for f in frames):found.append('missing sampled frame artifact')
        for window in coverage['windows']:
            observed=window.get('frame_times',[])
            if not observed or any(t not in times or not window['start']-.05<=t<=window['end']+.05 for t in observed):
                found.append('window lacks valid observed frames');break
            if observed!=sorted(set(observed)) or observed[0]-window['start']>1.2 or window['end']-observed[-1]>1.2 or any(b-a>1.2 for a,b in zip(observed,observed[1:])):
                found.append('window observed frame coverage is too sparse');break
    except (ValueError,KeyError,TypeError):found.append('invalid sampling artifact schema')
    return found

def audit(run):
    rows=source_records(ROOT)
    counts=collections.Counter();status=collections.Counter();issues=[];details=[]
    for row in rows:
        vid=video_id(row['filename']);home=run/vid
        if not (home/'manifest.json').exists():status['missing']+=1;continue
        manifest=json.loads((home/'manifest.json').read_text());status[manifest['status']]+=1
        if manifest['status']!='done':issues.append({'video_id':vid,'reason':'incomplete inference'});continue
        draft=json.loads((home/'draft.json').read_text());a=Annotation.model_validate(draft['annotation']).model_dump();c=draft['coverage']
        actual=[(w['start'],w['end']) for w in c['windows'] if w['status']=='done']
        if actual!=list(windows(c['duration'])) or c['gaps']:issues.append({'video_id':vid,'reason':'window coverage gap'})
        issues.extend({'video_id':vid,'reason':reason} for reason in sampling_issues(home,c,dataset_path(ROOT)/'open/videos'/row['filename']))
        if a['full_video_reviewed'] or a['status']=='reviewed':issues.append({'video_id':vid,'reason':'model marked reviewed'})
        if any(o['label']=='key' for o in a['objects']):issues.append({'video_id':vid,'reason':'unexpected automatic key'})
        for kind in ('objects','zones','states','events'):counts[kind]+=len(a[kind])
        masks=sum(bool(k.get('polygons')) for o in a['objects'] for k in o['keyframes']);counts['mask_keyframes']+=masks
        segment=json.loads((home/'segmentation.json').read_text()) if (home/'segmentation.json').exists() else {}
        if segment.get('status')!='done':issues.append({'video_id':vid,'reason':'segmentation incomplete'})
        details.append({'video_id':vid,'duration':c['duration'],'sampled_frames':len(c['sample_times']),'windows':len(actual),'refinements':len(c['refinements']),'refinement_errors':sum(bool(m.get('refinement_error')) for m in draft['metadata'].values()),'mask_keyframes':masks,'rejected_proposals':len(draft['diagnostics'].get('rejected',[])),'camera_change_suspected':bool(draft['diagnostics'].get('camera_changed'))})
    preservation={'snapshot_present':(run/'before.sqlite3').exists()}
    if preservation['snapshot_present']:
        with sqlite3.connect(run/'before.sqlite3') as before,sqlite3.connect(runtime_path(ROOT)/'annotations.sqlite3') as after:
            old={r[0]:r[1:] for r in before.execute('SELECT * FROM annotations')};new={r[0]:r[1:] for r in after.execute('SELECT * FROM annotations')}
            preservation.update(snapshot_rows=len(old),unchanged_rows=sum(new.get(k)==v for k,v in old.items()),changed_or_missing_ids=[k for k,v in old.items() if new.get(k)!=v])
    if not preservation['snapshot_present']:issues.append({'reason':'missing preservation snapshot'})
    elif preservation['changed_or_missing_ids']:issues.append({'reason':'existing annotations changed; inspect human history before certifying preservation','video_ids':preservation['changed_or_missing_ids']})
    return {'expected_videos':len(rows),'status_counts':dict(status),'proposal_counts':dict(counts),'issues':issues,'existing_annotations':preservation,'videos':details,'interpretation':'Execution and sampling coverage only; proposals are unverified and may miss or misclassify events.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);args=p.parse_args();run=Path(args.run).resolve();report=audit(run);write_json(run/'audit.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='videos'},ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['status_counts'].get('done')==report['expected_videos'] and not report['issues'] else 1)
if __name__=='__main__':main()
