"""Model proposals live separately. Only explicit revision-checked review writes human rows."""
import copy,json,sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone
from typing import Literal
from pydantic import Field
from fastapi import HTTPException
from .schema import Model,Annotation
from .geometry import validate_geometry

KINDS=('objects','zones','states','events')
def utc():return datetime.now(timezone.utc).isoformat()
class Draft(Model):
    run_id:str=Field(min_length=1,max_length=120)
    status:Literal['processing','done','failed']='processing'
    annotation:Annotation=Field(default_factory=Annotation)
    metadata:dict=Field(default_factory=dict)
    coverage:dict=Field(default_factory=dict)
    diagnostics:dict=Field(default_factory=dict)
class Review(Model):
    revision:int=Field(ge=0)
    draft_revision:int=Field(ge=1)
    action:Literal['accept','reject']
    ids:list[str]=Field(min_length=1,max_length=2000)
    reviewer:str=Field(min_length=1,max_length=200)
    overrides:dict=Field(default_factory=dict)
    confirm_visibility:bool=False
    confirm_zones:bool=False

def entities(a):return {x['id']:(kind,x) for kind in KINDS for x in a[kind]}
def dependencies(item,kind):
    if kind=='objects':return [item['zone_id']] if item.get('zone_id') else []
    if kind=='zones':return item.get('anchors',[])
    if kind=='states':return [item['target_id']]
    return [item[k] for k in ('actor_id','target_id','zone_id') if item.get(k)]
def closure(all_items,ids,terminal=()):
    found=set();pending=list(ids)
    while pending:
        i=pending.pop()
        if i in found:continue
        if i not in all_items:raise ValueError('Неизвестное предложение')
        found.add(i);kind,item=all_items[i]
        if i not in terminal:pending.extend(dependencies(item,kind))
    return sorted(found)

class DraftStore:
    def __init__(self,path):
        self.path=str(path)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('CREATE TABLE IF NOT EXISTS drafts(video_id TEXT PRIMARY KEY, revision INTEGER, body TEXT, updated_at TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS draft_reviews(video_id TEXT, run_id TEXT, proposal_id TEXT, decision TEXT, reviewer TEXT, annotation_revision INTEGER, updated_at TEXT, PRIMARY KEY(video_id,run_id,proposal_id))')
            if 'proposal_body' not in {r['name'] for r in c.execute('PRAGMA table_info(draft_reviews)')}:
                c.execute('ALTER TABLE draft_reviews ADD COLUMN proposal_body TEXT')
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def publish(self,vid,data):
        data=Draft.model_validate(data).model_dump()
        if data['annotation']['status']=='reviewed' or data['annotation']['full_video_reviewed']:raise ValueError('A model cannot mark a video reviewed')
        for z in data['annotation']['zones']:z['confirmed']=False
        for e in data['annotation']['events']:e['provenance']='ai_suggestion'
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            prev=c.execute('SELECT revision,body FROM drafts WHERE video_id=?',(vid,)).fetchone()
            if prev and json.loads(prev['body'])==data:return prev['revision']
            revision=(prev['revision'] if prev else 0)+1
            c.execute('INSERT INTO drafts VALUES(?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET revision=excluded.revision,body=excluded.body,updated_at=excluded.updated_at',(vid,revision,json.dumps(data,ensure_ascii=False),utc()))
        return revision
    def get(self,vid):
        with self.db() as c:
            row=c.execute('SELECT * FROM drafts WHERE video_id=?',(vid,)).fetchone()
            if not row:return None
            data=json.loads(row['body']); reviews=c.execute('SELECT proposal_id,decision,reviewer,annotation_revision,proposal_body FROM draft_reviews WHERE video_id=? AND run_id=?',(vid,data['run_id'])).fetchall()
        proposed=entities(data['annotation']);decisions={}
        for r in reviews:
            review=dict(r);body=review.pop('proposal_body');item=proposed.get(r['proposal_id'])
            snapshot={'entity':item[1],'metadata':data['metadata'].get(r['proposal_id'],{})} if item else None
            review['changed_since_review']=body is None or snapshot is None or json.loads(body)!=snapshot;decisions[r['proposal_id']]=review
        return {**data,'revision':row['revision'],'updated_at':row['updated_at'],'reviews':decisions}
    def summaries(self):
        with self.db() as c:rows=c.execute('SELECT video_id,body FROM drafts').fetchall()
        return {r['video_id']:{'status':(d:=json.loads(r['body']))['status'],'counts':{k:len(d['annotation'][k]) for k in KINDS}} for r in rows}
    def review(self,vid,request:Review,duration):
        if not request.reviewer.strip():raise HTTPException(422,'Укажите проверяющего')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM drafts WHERE video_id=?',(vid,)).fetchone()
            if not row:raise HTTPException(404,'Предложений пока нет')
            if row['revision']!=request.draft_revision:raise HTTPException(409,'Черновик обновился; перечитайте предложения')
            d=json.loads(row['body']);a=copy.deepcopy(d['annotation']);all_items=entities(a)
            if any(i not in all_items for i in request.ids):raise HTTPException(422,'Неизвестное предложение')
            human=c.execute('SELECT * FROM annotations WHERE video_id=?',(vid,)).fetchone()
            rev=human['revision'] if human else 0
            if rev!=request.revision:raise HTTPException(409,'Разметка изменена в другом окне; перечитайте видео')
            current=json.loads(human['body']) if human else Annotation().model_dump()
            for i,replacement in request.overrides.items():
                if i not in request.ids or replacement.get('id')!=i:raise HTTPException(422,'Исправлять можно только выбранное предложение')
                kind,_=all_items[i]
                a[kind]=[replacement if x['id']==i else x for x in a[kind]]
            try:a=Annotation.model_validate(a).model_dump()
            except ValueError as e:raise HTTPException(422,'Некорректное исправление: '+str(e)) from e
            decisions={r['proposal_id']:r['decision'] for r in c.execute('SELECT proposal_id,decision FROM draft_reviews WHERE video_id=? AND run_id=?',(vid,d['run_id']))}
            all_items=entities(a);ids=closure(all_items,request.ids,{i for i,v in decisions.items() if v=='accepted'}) if request.action=='accept' else list(set(request.ids))
            if any(decisions.get(i)=='accepted' for i in request.ids):raise HTTPException(409,'Предложение уже принято; правьте его в рабочей разметке')
            if request.action=='accept':
                if any(decisions.get(i)=='rejected' for i in ids):raise HTTPException(409,'Есть отклонённые зависимости; исправьте ссылки или создайте запись вручную')
                existing=entities(current)
                for i in ids:
                    kind,item=all_items[i]
                    if decisions.get(i)=='accepted':
                        if i not in existing:raise HTTPException(409,'Принятый опорный объект был удалён; исправьте ссылки')
                        continue
                    if i in existing:raise HTTPException(409,'ID уже существует в рабочей разметке')
                    if kind=='zones':
                        if not request.confirm_zones:raise HTTPException(422,'Подтвердите границы связанных зон')
                        item['confirmed']=True
                    if kind=='states' and item['value'].endswith('_absent'):
                        if not request.confirm_visibility:raise HTTPException(422,'Подтвердите полную видимость зоны на всём интервале отсутствия')
                        item['fully_visible']=True
                    current[kind].append(item)
                current['status']='in_progress';current['full_video_reviewed']=False
                current['reviewer']=request.reviewer.strip()
                try:
                    current=Annotation.model_validate(current).model_dump()
                    validate_geometry(current)
                except ValueError as e:raise HTTPException(422,str(e)) from e
                if any(x['end']>duration+.05 for k in ('states','events') for x in current[k]) or any(k['time']>duration+.05 for o in current['objects'] for k in o['keyframes']):raise HTTPException(422,'Время выходит за пределы видео')
                rev+=1;payload=json.dumps(current,ensure_ascii=False);stamp=utc()
                c.execute('INSERT INTO annotations VALUES(?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET revision=excluded.revision,body=excluded.body,updated_at=excluded.updated_at',(vid,rev,payload,stamp))
                c.execute('INSERT INTO history VALUES(?,?,?,?)',(vid,rev,payload,stamp))
            for i in ids:
                if decisions.get(i)=='accepted':continue
                c.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(video_id,run_id,proposal_id) DO UPDATE SET decision=excluded.decision,reviewer=excluded.reviewer,annotation_revision=excluded.annotation_revision,updated_at=excluded.updated_at,proposal_body=excluded.proposal_body',(vid,d['run_id'],i,'accepted' if request.action=='accept' else 'rejected',request.reviewer.strip(),rev,utc(),json.dumps({'entity':entities(d['annotation'])[i][1],'metadata':d['metadata'].get(i,{})},ensure_ascii=False)))
        return {'revision':rev,'affected_ids':ids,'action':request.action}
