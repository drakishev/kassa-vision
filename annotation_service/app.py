"""Local-only annotation workstation; original dataset files are read-only inputs."""
import csv, hashlib, json, os, sqlite3, subprocess, threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .schema import Annotation, SaveRequest, OBJECTS, ZONES, EVENTS, STATES, ROLES, ACTIONS, ITEMS, VISIBILITY, LABEL_CHECKS
from .rules import candidates
from .drafts import DraftStore, Review
from .geometry import validate_geometry
from .protocol import protocol_gaps

ROOT=Path(__file__).resolve().parents[1]

def create_app(dataset=None, runtime=None):
    dataset=Path(dataset or os.environ.get('ANNOTATION_DATASET',ROOT/'data/dataset')).resolve()
    runtime=Path(runtime or os.environ.get('ANNOTATION_RUNTIME',ROOT/'data/annotation_service')).resolve()
    runtime.mkdir(parents=True,exist_ok=True); (runtime/'media').mkdir(exist_ok=True)
    dbpath=runtime/'annotations.sqlite3'
    @contextmanager
    def db():
        c=sqlite3.connect(dbpath,timeout=15); c.row_factory=sqlite3.Row
        try:
            with c: yield c
        finally: c.close()
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS annotations(video_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS history(video_id TEXT, revision INTEGER, body TEXT, updated_at TEXT, PRIMARY KEY(video_id,revision))')
    drafts=DraftStore(dbpath)
    def read_json(path):
        return json.loads(path.read_text()) if path.exists() else []
    audit={r['filename']:r for r in read_json(dataset/'audit/labels_annotated.json')}
    final={r['filename']:r for r in read_json(dataset/'reference/labels_final.json')}
    manual={}
    manualfile=dataset/'reference/manual_review.csv'
    if manualfile.exists():
        with manualfile.open(encoding='utf-8-sig') as f:
            for row in csv.DictReader(f): manual[row.get('group_id','')]=row
    source=read_json(dataset/'reference/source_labels.json')
    files={p.name:p for p in (dataset/'open/videos').glob('*') if p.is_file() and p.suffix.lower() in ('.mov','.mp4','.mkv','.avi')}
    if not source: source=[{'filename':n} for n in sorted(files)]
    records={}
    for row in source:
        name=row['filename']
        if name not in files: continue
        vid=hashlib.sha256(name.encode()).hexdigest()[:20]
        records[vid]={'id':vid,'filename':name,'path':files[name],'source':row,'ai':final.get(name,audit.get(name,{})),'audit':audit.get(name,{}),'manual':manual.get(final.get(name,{}).get('group_id',''),{})}
    meta_cache={}; locks={vid:threading.Lock() for vid in records}
    def record(vid):
        if vid not in records: raise HTTPException(404,'Видео не найдено')
        return records[vid]
    def metadata(vid):
        if vid not in meta_cache:
            r=record(vid)
            try:
                result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height,avg_frame_rate:format=duration','-of','json',str(r['path'])],capture_output=True,text=True,check=True,timeout=30)
                m=json.loads(result.stdout); s=m['streams'][0]
                numerator,denominator=s.get('avg_frame_rate','30/1').split('/')
                meta_cache[vid]={'width':s['width'],'height':s['height'],'duration':float(m['format']['duration']),'fps':float(numerator)/max(float(denominator),1)}
            except (ValueError,KeyError,subprocess.SubprocessError,IndexError) as e: raise HTTPException(500,'Не удалось прочитать видео через ffprobe') from e
        return meta_cache[vid]
    def current(vid):
        with db() as c: row=c.execute('SELECT * FROM annotations WHERE video_id=?',(vid,)).fetchone()
        # Rows saved before the episode protocol lack its fields; fill defaults on read only.
        return {'revision':row['revision'],'annotation':{**Annotation().model_dump(),**json.loads(row['body'])},'updated_at':row['updated_at']} if row else {'revision':0,'annotation':Annotation().model_dump(),'updated_at':None}
    app=FastAPI(title='Касса · Разметка',docs_url='/api/docs')
    @app.middleware('http')
    async def local_boundary(request:Request,call_next):
        if request.method in ('PUT','POST','DELETE','PATCH'):
            origin=request.headers.get('origin')
            if origin and urlsplit(origin).netloc!=request.headers.get('host'): return JSONResponse({'detail':'Запрос с другого сайта запрещён'},status_code=403)
            if request.headers.get('sec-fetch-site')=='cross-site': return JSONResponse({'detail':'Запрос с другого сайта запрещён'},status_code=403)
            if int(request.headers.get('content-length','0') or 0)>8_000_000: return JSONResponse({'detail':'Слишком большой запрос'},status_code=413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['Cache-Control']='no-store'
        return response
    @app.get('/api/config')
    def config(): return {'objects':OBJECTS,'zones':ZONES,'events':EVENTS,'states':STATES,'roles':ROLES,'actions':ACTIONS,'items':ITEMS,'visibility':VISIBILITY,'label_checks':LABEL_CHECKS,'version':1}
    @app.get('/api/drafts/status')
    def draft_status(): return drafts.summaries()
    @app.get('/api/videos/{vid}/draft')
    def get_draft(vid:str):
        record(vid); return drafts.get(vid)
    @app.post('/api/videos/{vid}/draft/review')
    def review_draft(vid:str,body:Review):
        record(vid); return drafts.review(vid,body,metadata(vid)['duration'])
    @app.get('/api/videos')
    def videos():
        return [{**{k:r[k] for k in ('id','filename')},'source_label':r['source'].get('label',''),'ai_available':bool(r['ai']),'group_id':r['ai'].get('group_id',''),'fold':r['ai'].get('fold',''),'status':current(vid)['annotation']['status']} for vid,r in records.items()]
    @app.get('/api/videos/{vid}')
    def detail(vid:str):
        r=record(vid)
        return {'id':vid,'filename':r['filename'],'source':r['source'],'ai':r['ai'],'audit':r['audit'],'previous_review':r['manual'],'media':metadata(vid),**current(vid)}
    @app.put('/api/videos/{vid}/annotation')
    def save(vid:str,body:SaveRequest):
        record(vid); a=body.annotation; duration=metadata(vid)['duration']
        if any(s.end>duration+.05 for s in [*a.states,*a.events,*a.episodes]) or any(k.time>duration+.05 for o in a.objects for k in o.keyframes): raise HTTPException(422,'Время выходит за пределы видео')
        payload=a.model_dump_json(); now=datetime.now(timezone.utc).isoformat()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT revision FROM annotations WHERE video_id=?',(vid,)).fetchone(); rev=row['revision'] if row else 0
            if body.revision!=rev: raise HTTPException(409,'Разметка изменена в другом окне. Скачайте свой черновик и перечитайте серверную версию.')
            c.execute('INSERT INTO annotations VALUES(?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET revision=excluded.revision,body=excluded.body,updated_at=excluded.updated_at',(vid,rev+1,payload,now))
            c.execute('INSERT INTO history VALUES(?,?,?,?)',(vid,rev+1,payload,now))
        return {'revision':rev+1,'updated_at':now}
    @app.get('/api/videos/{vid}/candidates')
    def rules(vid:str):
        record(vid); return candidates(current(vid)['annotation'])
    @app.get('/api/videos/{vid}/media')
    def media(vid:str):
        r=record(vid); stat=r['path'].stat(); dest=runtime/'media'/f'{vid}-{stat.st_size}-{stat.st_mtime_ns}.mp4'
        with locks[vid]:
            if not dest.exists():
                tmp=dest.with_suffix('.tmp.mp4')
                base=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-i',str(r['path']),'-map','0:v:0','-an']
                try:
                    subprocess.run(base+['-c:v','copy','-movflags','+faststart',str(tmp)],capture_output=True,check=True,timeout=120)
                    tmp.replace(dest)
                except subprocess.SubprocessError as e:
                    tmp.unlink(missing_ok=True)
                    raise HTTPException(500,'Не удалось подготовить MP4. Исходный файл сохранён без изменений.') from e
        return FileResponse(dest,media_type='video/mp4')
    @app.get('/api/videos/{vid}/frame')
    def frame(vid:str,t:float=0):
        r=record(vid); m=metadata(vid); t=round(max(0,min(t,max(0,m['duration']-1/max(m['fps'],1)))),3)
        folder=runtime/'frames'/vid; folder.mkdir(parents=True,exist_ok=True); dest=folder/f'{t:010.3f}.jpg'
        with locks[vid]:
            if not dest.exists():
                try: subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-ss',str(t),'-i',str(r['path']),'-frames:v','1','-q:v','2',str(dest)],capture_output=True,check=True,timeout=30)
                except subprocess.SubprocessError as e: raise HTTPException(500,'Не удалось извлечь кадр') from e
        if not dest.exists(): raise HTTPException(404,'Кадр недоступен')
        return FileResponse(dest,media_type='image/jpeg')
    @app.get('/api/export/episodes')
    def export_episodes():
        """Human episode records only; AI drafts and model descriptions are excluded."""
        rows=[]
        for vid,r in records.items():
            a=current(vid)['annotation'];people={p['id']:p for p in a['participants']}
            name=lambda i:people[i]['name'] if i in people else ''
            rows.append({'video_id':vid,'filename':r['filename'],'group_id':r['ai'].get('group_id',''),'fold':r['ai'].get('fold',''),
                'organizer_label':r['source'].get('label',''),'organizer_timecode':r['source'].get('timecode_raw',r['source'].get('timecode_sec','')),
                'status':a['status'],'reviewer':a['reviewer'],'full_video_reviewed':a['full_video_reviewed'],'verdict':a['verdict'],
                'label_check':a['label_check'],'participants':a['participants'],
                'episodes':[{**e,'actor':name(e['actor_id']),'recipient':name(e['recipient_id'])} for e in sorted(a['episodes'],key=lambda e:e['start'])],
                'gaps':protocol_gaps(a,r['source'].get('label',''))})
        payload={'schema_version':1,'exported_at':datetime.now(timezone.utc).isoformat(),'time_unit':'seconds_from_original_video_start',
                 'taxonomy':{k:config()[k] for k in ('roles','actions','items','visibility','label_checks')},
                 'complete_videos':sum(not x['gaps'] for x in rows),'videos':rows}
        return JSONResponse(payload,headers={'Content-Disposition':'attachment; filename="episodes.json"'})
    @app.get('/api/export')
    def export():
        payload={'schema_version':1,'exported_at':datetime.now(timezone.utc).isoformat(),'coordinate_system':'normalized_xywh_and_polygon_xy; original_video; seconds','taxonomy':config(),'videos':[]}
        for vid,r in records.items(): payload['videos'].append({'video_id':vid,'filename':r['filename'],'source':r['source'],'group_id':r['ai'].get('group_id',''),'fold':r['ai'].get('fold',''),**current(vid)})
        return JSONResponse(payload,headers={'Content-Disposition':'attachment; filename="human-annotations.json"'})
    @app.get('/api/export/coco')
    def coco(request:Request,reviewed_only:bool=True):
        categories=[{'id':i+1,'name':name} for i,name in enumerate(OBJECTS)]; cat={c['name']:c['id'] for c in categories}
        images=[]; boxes=[]
        for vid,r in records.items():
            a=current(vid)['annotation']
            if reviewed_only and a['status']!='reviewed': continue
            try:validate_geometry(Annotation.model_validate(a).model_dump())
            except ValueError as e:raise HTTPException(422,f'Исправьте геометрию видео {vid} перед экспортом: {e}') from e
            if not a['objects']: continue
            m=metadata(vid); frames={}
            for o in a['objects']:
                for k in o['keyframes']:
                    t=k['time']
                    if t not in frames:
                        frames[t]=len(images)+1; images.append({'id':frames[t],'file_name':f'{vid}/{t:010.3f}.jpg','width':m['width'],'height':m['height'],'video_id':vid,'source_video':r['filename'],'time_sec':t,'frame_url':f'/api/videos/{vid}/frame?t={t}','group_id':r['ai'].get('group_id',''),'scene_id':a.get('scene_id',''),'fold':r['ai'].get('fold','')})
                    x,y,w,h=k['box']; bbox=[x*m['width'],y*m['height'],w*m['width'],h*m['height']]
                    item={'id':len(boxes)+1,'image_id':frames[t],'category_id':cat[o['label']],'bbox':bbox,'area':bbox[2]*bbox[3],'iscrowd':0,'track_id':o['id']}
                    if k.get('polygons'):
                        polygons=[[[x*m['width'],y*m['height']] for x,y in polygon] for polygon in k['polygons']]
                        item['segmentation']=[[v for point in polygon for v in point] for polygon in polygons]
                        item['area']=sum(abs(sum(p[i][0]*p[(i+1)%len(p)][1]-p[(i+1)%len(p)][0]*p[i][1] for i in range(len(p))))/2 for p in polygons)
                        item['mask_source']=k.get('mask_source','')
                    boxes.append(item)
        prefix=request.scope.get('root_path','').rstrip('/')
        for item in images:item['frame_url']=prefix+item['frame_url']
        return JSONResponse({'info':{'description':'Explicit keyframes from working annotations, including human-accepted AI proposals; not exhaustive frame labels. Exterior segmentation contours omit holes. Download images using frame_url. Split by group_id and scene_id, not individual frames.'},'images':images,'annotations':boxes,'categories':categories},headers={'Content-Disposition':'attachment; filename="keyframes-coco.json"'})
    app.mount('/',StaticFiles(directory=Path(__file__).parent/'static',html=True),name='ui')
    return app
