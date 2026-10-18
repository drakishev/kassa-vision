"""Isolated UI contract fixture. No real videos, real jobs or GPU models."""
import asyncio
import base64
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from fastapi.responses import JSONResponse
from service.accounts import AccountStore
from service.config import Settings
from service.events import public_violation
from service.jobs import JobStore
from service.cancellation import CANCELLED_MESSAGE
from service.ui_gateway import create_ui

TEMP=tempfile.TemporaryDirectory(prefix='kassa-ui-fixture-')
settings=Settings(runtime=Path(TEMP.name),api_key='synthetic-fixture-only')
store=JobStore(settings.database)
accounts=AccountStore(settings.runtime/'ui/sessions.sqlite3')
accounts.create_user('tester','synthetic-password-42')
accounts.create_user('other','synthetic-password-42')
fixture=Path(TEMP.name)/'fixture.mov'
subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=gray:s=192x320:r=30:d=1','-c:v','libx264','-threads','1',str(fixture)],check=True)
image=Image.new('RGB',(192,320),(215,221,228));stream=io.BytesIO();image.save(stream,format='JPEG')
JPEG=base64.b64encode(stream.getvalue()).decode()
jobs={}

def complete_result(job_id):
    record=jobs[job_id]
    events=[]
    if record['mode']=='positive':
        run=settings.runtime/'jobs'/job_id/'attempt_1';run.mkdir(exist_ok=True)
        frames=[{'time_sec':t,'image':f'{i}.jpg'} for i,t in enumerate([.1,.3,.7])]
        for frame in frames:(run/frame['image']).write_bytes(base64.b64decode(JPEG))
        model_events=[{'type':'V05','time_sec':.3,
            'visible_evidence':'Синтетический пример, не результат анализа банковского видео.',
            'explanation':'Тестовая причина классификации, сформированная только для проверки интерфейса.'},
            {'type':'Второй тестовый эпизод','time_sec':.7,
             'visible_evidence':'Второе синтетическое наблюдение.',
             'explanation':'Причина второго синтетического эпизода.'}]
        (run/'evidence.json').write_text(json.dumps({'frames':frames}))
        (run/'window_000.json').write_text(json.dumps({'events':model_events}))
        events=[public_violation(event,frames,run) for event in model_events]
    store.finish(job_id,{'verdict':'violation' if events else 'no_violation','violations':events,'warnings':['Контрактный тест на искусственных данных.']})

async def work(job_id):
    with store.connect() as db:
        db.execute("UPDATE jobs SET state='running',attempts=1 WHERE job_id=? AND state='queued'",(job_id,))
    await asyncio.sleep(1.5)
    record=jobs[job_id]
    # The late-cancel fixture may already have completed this job while we slept.
    if record.get('hold') or store.public_job(job_id)['status']!='processing':return
    if record['mode']=='failed':store.fail(job_id,'Synthetic failure')
    else:complete_result(job_id)

async def upstream(request):
    if request.url.path=='/readyz':return httpx.Response(200,json={'ready':True})
    if request.method=='POST' and request.url.path.endswith('/cancel'):
        job_id=request.url.path.split('/')[-2]
        if job_id not in jobs:return httpx.Response(404,json={})
        record=jobs[job_id]
        if record.get('late_cancel'):
            record['hold']=False;record['mode']='normal'
            complete_result(job_id)
            return httpx.Response(200,json=store.public_job(job_id))
        if record.get('cancel_error'):
            record['cancel_error']=False
            return httpx.Response(502,json={'error':{'message':'Synthetic transient failure'}})
        record['cancelled']=True
        store.cancel(job_id)
        return httpx.Response(200,json=store.public_job(job_id))
    if request.method=='POST':
        body=await request.aread()
        if b'queue.mov' in body:return httpx.Response(503,json={'error':{'message':'Queue full'}})
        if b'broken.mov' in body:return httpx.Response(422,json={'error':{'message':'Invalid fixture'}})
        if b'cancel-upload.mov' in body:await asyncio.sleep(1)
        job_id=uuid4().hex
        folder=settings.runtime/'jobs'/job_id;folder.mkdir(parents=True)
        (folder/'input.mov').write_bytes(fixture.read_bytes())
        store.enqueue(job_id,str(folder/'input.mov'),{'duration_sec':1,'width':192,'height':320},100)
        mode='failed' if b'failed.mov' in body else 'normal' if b'normal.mov' in body else 'positive'
        jobs[job_id]={'mode':mode,'hold':b'cancel' in body,'cancel_error':b'cancel-error.mov' in body,'late_cancel':b'late-cancel.mov' in body,'poll_error':b'poll-cancel.mov' in body}
        asyncio.create_task(work(job_id))
        return httpx.Response(202,json={'job_id':job_id,'status':'processing'})
    return httpx.Response(404,json={})

app=create_ui(settings,transport=httpx.MockTransport(upstream),dist=ROOT/'frontend/dist')
@app.middleware('http')
async def synthetic_poll_failure(request,call_next):
    if request.method=='GET' and request.url.path.startswith('/ui/api/jobs/'):
        record=jobs.get(request.url.path.split('/')[-1],{})
        if record.get('poll_error') and not record.get('cancelled'):
            return JSONResponse({'error':{'message':'Synthetic poll failure'}},status_code=502)
    return await call_next(request)
