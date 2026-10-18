"""Observable conditions only; these are not legal violation decisions."""

def merge(spans):
    out=[]
    for a,b in sorted(spans):
        if out and a<=out[-1][1]+1e-6: out[-1][1]=max(out[-1][1],b)
        else: out.append([a,b])
    return out

def subtract(spans,blocks):
    for c,d in merge(blocks):
        parts=[]
        for a,b in spans:
            if d<=a or c>=b: parts.append([a,b]); continue
            if a<c: parts.append([a,c])
            if d<b: parts.append([d,b])
        spans=parts
    return spans

def candidates(a):
    states=a['states']; zones={z['id']:z for z in a['zones'] if z['confirmed']}; objects={o['id']:o for o in a['objects']}
    def spans(target,value,blockvalues,visible=False):
        good=merge([(s['start'],s['end']) for s in states if s['target_id']==target and s['value']==value and (not visible or s['fully_visible'])])
        blocked=[(s['start'],s['end']) for s in states if s['target_id']==target and s['value'] in blockvalues]
        return subtract(good,blocked)
    out=[]
    for target in list(objects.values())+list(zones.values()):
        oid=target['id']; label=target['label']; zid=target.get('zone_id',oid if oid in zones else '')
        if zid not in zones: continue
        checks=[]
        if label in ('safe','safe_door','cash_drawer'): checks.append(('safe','open',['closed','unknown']))
        if label=='room_door': checks.append(('door','open',['closed','unknown']))
        if oid in zones or label in ('cash','table','transfer_tray'): checks.append(('cash','cash_present',['cash_absent','unknown']))
        absent=spans(zid,'person_absent',['person_present','unknown'],True)
        for kind,value,blocks in checks:
            active=spans(oid,value,blocks)
            overlaps=merge([(max(s,u),min(e,v)) for s,e in active for u,v in absent if min(e,v)>max(s,u)])
            for start,end in overlaps:
                if end-start+1e-6<a['thresholds'][kind]: continue
                out.append({'label':kind+'_unattended','target_id':oid,'zone_id':zid,'start':start,'end':end,'threshold':a['thresholds'][kind], 'note':'Условие по ручным интервалам; требуется проверка контекста. Не является автоматическим выводом о нарушении.'})
    return out
