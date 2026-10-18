"""Reproducible local checks; never writes human labels or infers GT accuracy."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

from annotation_service.drafts import dependencies, entities
from annotation_service.schema import Annotation
from annotation_service.geometry import area, crossing
from .common import write_json

PAIRS = {frozenset(p) for p in [('open', 'closed'), ('person_present', 'person_absent'),
                               ('cash_present', 'cash_absent')]}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def structural(draft):
    """All proposals and keyframes, reporting exact IDs but no private prose."""
    a = Annotation.model_validate(draft['annotation']).model_dump()
    duration = draft['coverage']['duration']
    if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:
        raise ValueError('Video duration must be finite and positive')
    items = entities(a)
    issues = []
    counts = collections.Counter({k: len(a[k]) for k in a if k in ('objects', 'zones', 'states', 'events')})

    def add(code, ids, **details):
        issues.append({'code': code, 'ids': ids, **details})

    for oid, (kind, item) in items.items():
        for ref in dependencies(item, kind):
            if ref not in items:
                add('missing_dependency', [oid], target=ref)
        md = draft.get('metadata', {}).get(oid, {})
        if md.get('refinement_error'):
            add('refinement_error', [oid])
        if kind in ('objects', 'zones'):
            polygons = [item['points']] if kind == 'zones' else [p for k in item['keyframes'] for p in k.get('polygons', [])]
            for p in polygons:
                counts['polygons'] += 1
                if area(p) < 1e-8:
                    add('zero_area_polygon', [oid])
                if crossing(p):
                    add('self_intersecting_polygon', [oid])
        if kind == 'objects':
            keys = item['keyframes']
            counts['keyframes'] += len(keys)
            counts['mask_keyframes'] += sum(bool(k.get('polygons')) for k in keys)
            if not keys:
                add('no_object_keyframes', [oid])
            if [k['time'] for k in keys] != sorted(k['time'] for k in keys):
                add('unordered_keyframes', [oid])
            for key in keys:
                if key['time'] > duration + .05:
                    add('keyframe_outside_video', [oid], time=key['time'])
                x, y, w, h = key['box']
                if any(not (x-1e-5 <= px <= x+w+1e-5 and y-1e-5 <= py <= y+h+1e-5)
                       for p in key.get('polygons', []) for px, py in p):
                    add('mask_outside_box', [oid], time=key['time'])
        if kind in ('states', 'events'):
            if item['end'] > duration + .05:
                add('interval_outside_video', [oid])
            if kind == 'events' and item['end'] == item['start']:
                add('instant_event_boundary', [oid])
            # An interval has no continuous visibility proof simply because the VLM said so.
            if kind == 'states' and item['value'].endswith('_absent'):
                add('absence_needs_continuous_visibility', [oid])
            if kind == 'events' and item['label'] == 'cash_transfer':
                t = md.get('transfer', {})
                if not (t.get('cash_visible') is True and t.get('release_receive_visible') is True
                        and all(t.get(k) for k in ('before', 'interaction', 'after', 'holder_before', 'holder_after'))
                        and t['holder_before'] != t['holder_after']):
                    add('transfer_missing_three_phases', [oid])
            evidence = md.get('evidence_times', [])
            if not evidence:
                add('missing_evidence', [oid])
            if any(not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0 or t > duration+.05 for t in evidence):
                add('invalid_evidence_time', [oid])
    for i, left in enumerate(a['states']):
        for right in a['states'][i+1:]:
            if left['target_id'] != right['target_id']:
                continue
            overlap = min(left['end'], right['end']) - max(left['start'], right['start'])
            point_conflict=(left['start']==left['end'] and right['start']<left['start']<right['end']) or (right['start']==right['end'] and left['start']<right['start']<left['end']) or (left['start']==left['end']==right['start']==right['end'])
            shared=set(draft.get('metadata',{}).get(left['id'],{}).get('evidence_times',[])) & set(draft.get('metadata',{}).get(right['id'],{}).get('evidence_times',[]))
            same_frame=any(max(left['start'],right['start'])<=t<=min(left['end'],right['end']) for t in shared)
            if (overlap > .05 or point_conflict or same_frame) and frozenset((left['value'], right['value'])) in PAIRS:
                add('contradictory_states', [left['id'], right['id']], overlap_sec=round(overlap, 4))
    # Same-action overlaps with the same known participants may be duplicate hypotheses.
    for i, left in enumerate(a['events']):
        for right in a['events'][i+1:]:
            if left['label'] != right['label'] or not (left['actor_id'] or left['target_id']):
                continue
            if any(left[k] != right[k] for k in ('actor_id', 'target_id', 'zone_id')):
                continue
            overlap = min(left['end'], right['end']) - max(left['start'], right['start'])
            if overlap > .05:
                add('overlapping_event_hypotheses', [left['id'], right['id']], overlap_sec=round(overlap, 4))
    return {'counts': dict(counts), 'issues': issues, 'evaluation_type': 'self_supervised_proxy'}


def run_structural(source, output):
    counts, issues = collections.Counter(), collections.Counter()
    reports = []
    for path in sorted(source.glob('*/draft.json')):
        report = structural(json.loads(path.read_text()))
        report.update(video_id=path.parent.name, input_sha256=digest(path), checker_sha256=digest(__file__))
        write_json(output/path.parent.name/'structural.json', report)
        counts.update(report['counts'])
        issues.update(i['code'] for i in report['issues'])
        reports.append({'video_id': path.parent.name, 'input_sha256': report['input_sha256'],
                        'issues': len(report['issues'])})
    result = {'videos': reports, 'video_count': len(reports), 'counts': dict(counts),
              'issue_counts': dict(issues), 'interpretation': 'Structural checks and uncertainty flags, not semantic accuracy.'}
    write_json(output/'structural_summary.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = run_structural(args.source, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != 'videos'}, indent=2))


if __name__ == '__main__':
    main()
