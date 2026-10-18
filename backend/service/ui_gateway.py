"""Same-origin UI adapter; server key and source videos never become public assets."""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .config import ROOT, Settings
from .accounts import AccountStore, InvalidCredentials, LoginThrottled, SESSION_TTL
from .jobs import JobStore
from .limits import UploadBodyLimit, route_path
from .presentation import enrich_review
from .jury_gateway import register_jury_api, api_error

COOKIE='kassa_review'
JOB=re.compile(r'^[a-f0-9]{32}$')
class LoginInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:str=Field(min_length=1,max_length=32)
    password:str=Field(min_length=1,max_length=128)


def create_ui(settings=None, *, transport=None, dist=None, account_runtime=None, api_base_url=None, annotation_dataset=None, annotation_runtime=None):
    settings=settings or Settings()
    runtime=settings.runtime/'ui';runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    account_runtime=Path(account_runtime or runtime)
    database=account_runtime/'sessions.sqlite3'
    accounts=AccountStore(database)
    jobs=JobStore(settings.database)
    accounts.bootstrap(settings.database,account_runtime/'admin-credentials.json')
    api_url=api_base_url or os.environ.get('KASSA_API_URL','http://127.0.0.1:18861')
    parsed=urlsplit(api_url)
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','::1'} or parsed.username:
        raise ValueError('UI upstream must be loopback HTTP')
    dist=Path(dist or os.environ.get('KASSA_UI_DIST',ROOT.parent/'frontend/dist'))
    app=FastAPI(title='Kassa Vision · Проверка видео',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.accounts=accounts
    locks={}

    def owns(request,job_id):
        user=getattr(request.state,'user',None)
        return bool(user and JOB.fullmatch(job_id) and accounts.owns(user['id'],job_id))

    def failure(status,message):
        return secure(JSONResponse({'error':{'message':message}},status_code=status))

    def secure(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.middleware('http')
    async def boundary(request,call_next):
        path=route_path(request.scope)
        def reject(status,message):
            if path.startswith('/api/v1/'):
                code={400:'INVALID_LENGTH',403:'FORBIDDEN',413:'FILE_TOO_LARGE'}[status]
                return secure(api_error(status,code,message))
            return failure(status,message)
        is_annotation=path=='/annotate' or path.startswith('/annotate/')
        if path.startswith('/ui/api/') or is_annotation:
            request.state.user=await run_in_threadpool(accounts.user,request.cookies.get(COOKIE))
            if is_annotation and not request.state.user:
                if path in {'/annotate','/annotate/'} and request.method=='GET':
                    return secure(RedirectResponse('/',status_code=303))
                return failure(401,'Войдите в учётную запись.')
            if path not in {'/ui/api/auth/me','/ui/api/auth/login'} and not request.state.user:
                return failure(401,'Войдите в учётную запись.')
        if request.method=='POST':
            origin=request.headers.get('origin')
            if origin and origin.rstrip('/')!=f'{request.url.scheme}://{request.url.netloc}':
                return reject(403,'Запрос с другого сайта запрещён.')
            if request.headers.get('sec-fetch-site')=='cross-site':return reject(403,'Запрос с другого сайта запрещён.')
            try:
                if int(request.headers.get('content-length','0'))>settings.max_upload_bytes+1024*1024:
                    return reject(413,'Видео превышает допустимый размер.')
            except ValueError:return reject(400,'Некорректный размер запроса.')
        response=await call_next(request)
        if getattr(request.state,'body_limit_exceeded',False):return reject(413,'Видео превышает допустимый размер.')
        if request.url.scheme=='https':response.headers['Strict-Transport-Security']='max-age=31536000'
        return secure(response)

    def client():
        return httpx.AsyncClient(base_url=api_url,transport=transport,trust_env=False,
            timeout=httpx.Timeout(120,connect=5),headers={'X-API-Key':settings.api_key})

    @app.get('/ui/api/config')
    async def config(request:Request):
        ready=False
        try:
            async with client() as upstream:
                r=await upstream.get('/readyz',timeout=5);ready=r.status_code==200
        except httpx.HTTPError:pass
        response=JSONResponse({'max_upload_bytes':settings.max_upload_bytes,
            'max_duration_sec':settings.max_duration_sec,'ready':ready})
        return response

    @app.get('/ui/api/auth/me')
    async def me(request:Request):
        return {'user':request.state.user}

    @app.post('/ui/api/auth/login')
    async def login(request:Request):
        body=bytearray()
        async for chunk in request.stream():
            if len(body)+len(chunk)>4096:return failure(413,'Слишком большой запрос входа.')
            body.extend(chunk)
        try:credentials=LoginInput.model_validate_json(body)
        except ValidationError:return failure(400,'Укажите логин и пароль.')
        try:
            token=await run_in_threadpool(accounts.login,credentials.username,credentials.password,
                request.client.host if request.client else 'unknown',request.cookies.get(COOKIE))
        except InvalidCredentials:return failure(401,'Неверный логин или пароль.')
        except LoginThrottled:
            response=failure(429,'Слишком много попыток входа. Повторите через пять минут.')
            response.headers['Retry-After']='300';return response
        response=JSONResponse({'user':accounts.user(token)})
        response.set_cookie(COOKIE,token,max_age=SESSION_TTL,httponly=True,samesite='strict',
                            secure=request.url.scheme=='https')
        return response

    @app.post('/ui/api/auth/logout')
    async def logout(request:Request):
        await run_in_threadpool(accounts.logout,request.cookies.get(COOKIE))
        response=JSONResponse({'ok':True});response.delete_cookie(COOKIE)
        return response

    @app.get('/ui/api/history')
    async def history(request:Request,limit:int=Query(20,ge=1,le=50),offset:int=Query(0,ge=0,le=1000000)):
        return await run_in_threadpool(accounts.history,request.state.user['id'],settings.database,limit,offset)

    @app.post('/ui/api/analyze')
    async def analyze(request:Request,file:UploadFile=File(...)):
        try:
            if not (file.filename or '').lower().endswith('.mov'):return failure(415,'Выберите видео в формате .mov.')
            file.file.seek(0,2);size=file.file.tell();file.file.seek(0)
            if size>settings.max_upload_bytes:return failure(413,'Видео превышает допустимый размер.')
            async with client() as upstream:
                response=await upstream.post('/api/v1/analyze',files={'file':(file.filename,file.file,'video/quicktime')})
            if response.status_code==202:
                result=response.json();job_id=result.get('job_id','') if isinstance(result,dict) else ''
                if not isinstance(job_id,str) or not JOB.fullmatch(job_id):return failure(502,'Сервис вернул некорректный идентификатор задания.')
                await run_in_threadpool(accounts.bind_job,job_id,request.state.user['id'],file.filename or 'video.mov')
            return JSONResponse(response.json(),status_code=response.status_code)
        except (httpx.HTTPError,json.JSONDecodeError):return failure(502,'Сервис анализа временно недоступен. Повторите попытку позже.')
        finally:await file.close()

    async def reviewed(request,job_id,result):
        result=await run_in_threadpool(enrich_review,settings,job_id,result)
        return {**result,'filename':accounts.filename(request.state.user['id'],job_id)}

    @app.get('/ui/api/jobs/{job_id}')
    async def job(request:Request,job_id:str):
        if not owns(request,job_id):return failure(404,'Задание недоступно для этой учётной записи.')
        result=await run_in_threadpool(jobs.public_job,job_id)
        if result is None:return failure(404,'Задание не найдено.')
        return await reviewed(request,job_id,result)

    @app.get('/ui/api/jobs/{job_id}/diagnostics')
    async def diagnostics(request:Request,job_id:str):
        if not owns(request,job_id):return failure(404,'Задание недоступно для этой учётной записи.')
        try:
            async with client() as upstream:
                response=await upstream.get('/api/v1/jobs/'+job_id+'/diagnostics')
            return JSONResponse(response.json(),status_code=response.status_code,headers={'Cache-Control':'private, no-store'})
        except (httpx.HTTPError,json.JSONDecodeError):
            return failure(502,'Диагностика временно недоступна.')

    @app.get('/ui/api/jobs/{job_id}/diagnostics/frames/{frame_index}')
    async def diagnostic_frame(request:Request,job_id:str,frame_index:int):
        if not owns(request,job_id):return failure(404,'Задание недоступно для этой учётной записи.')
        try:
            async with client() as upstream:
                response=await upstream.get('/api/v1/jobs/'+job_id+'/diagnostics/frames/'+str(frame_index))
            if response.status_code!=200:return JSONResponse(response.json(),status_code=response.status_code)
            if response.headers.get('content-type','').split(';')[0]!='image/jpeg':
                return failure(502,'Некорректный формат кадра.')
            return Response(response.content,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})
        except (httpx.HTTPError,json.JSONDecodeError):
            return failure(502,'Кадр временно недоступен.')

    @app.post('/ui/api/jobs/{job_id}/cancel')
    async def cancel(request:Request,job_id:str):
        if not owns(request,job_id):return failure(404,'Задание недоступно для этой учётной записи.')
        try:
            async with client() as upstream:
                response=await upstream.post('/api/v1/jobs/'+job_id+'/cancel',timeout=15)
            result=response.json()
            if response.status_code==200:result=await reviewed(request,job_id,result)
            return JSONResponse(result,status_code=response.status_code)
        except (httpx.HTTPError,json.JSONDecodeError):
            return failure(502,'Не удалось подтвердить отмену. Повторите запрос.')

    def browser_media(job_id):
        # Source is looked up only after browser ownership is verified.
        with sqlite3.connect(f'file:{settings.database}?mode=ro',uri=True) as db:
            row=db.execute('SELECT input_path FROM jobs WHERE job_id=?',(job_id,)).fetchone()
        if not row:raise FileNotFoundError()
        source=Path(row[0]).resolve(strict=True)
        if not source.is_relative_to((settings.runtime/'jobs').resolve()):raise ValueError('Invalid job path')
        target=runtime/(job_id+'.mp4')
        if target.exists():return target
        staging=runtime/(job_id+'.partial.mp4')
        try:
            subprocess.run(['ffmpeg','-v','error','-i',str(source),'-map','0:v:0','-an',
                '-c:v','copy','-movflags','+faststart','-y',str(staging)],
                check=True,capture_output=True,timeout=60)
            os.replace(staging,target)
        finally:staging.unlink(missing_ok=True)
        return target

    @app.get('/ui/api/jobs/{job_id}/media')
    async def media(request:Request,job_id:str):
        if not owns(request,job_id):return failure(404,'Видео недоступно для этой учётной записи.')
        async with locks.setdefault(job_id,asyncio.Lock()):
            try:path=await run_in_threadpool(browser_media,job_id)
            except (FileNotFoundError,ValueError,sqlite3.Error,subprocess.SubprocessError):
                return failure(422,'Предпросмотр недоступен. Используйте кадры-доказательства.')
        return FileResponse(path,media_type='video/mp4')

    if (dist/'assets').exists():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
    @app.get('/documents')
    @app.get('/documents/')
    @app.get('/history')
    @app.get('/v2/history')
    @app.get('/v2')
    @app.get('/v2/')
    @app.get('/')
    def index():
        if not (dist/'index.html').exists():return failure(503,'Сначала выполните сборку интерфейса.')
        return FileResponse(dist/'index.html')
    @app.get('/favicon.svg')
    def favicon():return FileResponse(dist/'favicon.svg',media_type='image/svg+xml')
    register_jury_api(app,settings,api_url,transport)
    from annotation_service.app import create_app as create_annotator
    app.mount('/annotate',create_annotator(
        dataset=annotation_dataset,
        runtime=annotation_runtime or os.environ.get('ANNOTATION_RUNTIME',settings.runtime/'annotations'),
    ))
    app.add_middleware(UploadBodyLimit,settings=settings,paths=('/ui/api/analyze','/api/v1/analyze'))
    return app


def main():
    import uvicorn
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=18863)
    args=parser.parse_args()
    uvicorn.run(create_ui(),host=args.host,port=args.port,access_log=False)

if __name__=='__main__':main()
