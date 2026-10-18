import tempfile
import unittest
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from service.config import Settings
from service.accounts import AccountStore
from service.jobs import JobStore
from service.ui_gateway import create_ui

JOB='a'*32

class UIGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.seen=[]
        self.settings=Settings(runtime=Path(self.tmp.name),api_key='server-only-secret')
        accounts=AccountStore(self.settings.runtime/'ui/sessions.sqlite3')
        accounts.create_user('tester','synthetic-password-42')
        accounts.create_user('other','synthetic-password-42')
        self.jobs=JobStore(self.settings.database)
        async def upstream(request):
            self.seen.append((request.url.path,request.headers.get('x-api-key')))
            await request.aread()
            if request.url.path=='/readyz':return httpx.Response(200,json={'ready':True})
            if request.method=='POST':
                if request.url.path=='/api/v1/analyze':
                    self.jobs.enqueue(JOB,'/synthetic.mov',{})
                    self.jobs.claim();self.jobs.finish(JOB,{'verdict':'no_violation','violations':[]})
                return httpx.Response(202,json={'job_id':JOB,'status':'processing'})
            return httpx.Response(200,json={'job_id':JOB,'status':'done','verdict':'no_violation','violations':[]})
        self.app=create_ui(self.settings,transport=httpx.MockTransport(upstream))
        self.client=TestClient(self.app)
        self.client.post('/ui/api/auth/login',json={'username':'tester','password':'synthetic-password-42'})

    def tearDown(self):
        self.client.close();self.tmp.cleanup()

    def test_documents_direct_links_serve_the_app_shell(self):
        dist=Path(self.tmp.name)/'dist';dist.mkdir()
        (dist/'index.html').write_text('<html>Synthetic app shell</html>')
        with TestClient(create_ui(self.settings,dist=dist)) as client:
            for path in ['/documents','/documents/']:
                response=client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.text,'<html>Synthetic app shell</html>')
                self.assertIn('no-store',response.headers['cache-control'])

    def test_upload_uses_server_key_and_binds_result_to_browser(self):
        config=self.client.get('/ui/api/config')
        self.assertNotIn('secret',config.text)
        response=self.client.post('/ui/api/analyze',files={'file':('test.mov',b'fixture','video/quicktime')})
        self.assertEqual(response.status_code,202)
        self.assertIn(('/api/v1/analyze','server-only-secret'),self.seen)
        self.assertEqual(self.client.get('/ui/api/jobs/'+JOB).status_code,200)
        other=TestClient(self.app)
        other.post('/ui/api/auth/login',json={'username':'other','password':'synthetic-password-42'})
        self.assertEqual(other.get('/ui/api/jobs/'+JOB).status_code,404)
        other.close()

    def test_diagnostics_and_saved_frames_require_owner_and_proxy_exact_image(self):
        self.client.post('/ui/api/analyze',files={'file':('synthetic.mov',b'fixture')})
        async def upstream(request):
            if '/diagnostics/frames/' in request.url.path:
                return httpx.Response(200,content=b'synthetic-jpeg',headers={'content-type':'image/jpeg'})
            return httpx.Response(200,json={'job_id':JOB,'frames':[],'capabilities':{}})
        with TestClient(create_ui(self.settings,transport=httpx.MockTransport(upstream))) as owner:
            owner.post('/ui/api/auth/login',json={'username':'tester','password':'synthetic-password-42'})
            for suffix in ['/diagnostics','/diagnostics/frames/0']:
                self.assertEqual(owner.get('/ui/api/jobs/'+JOB+suffix).status_code,200)
            frame=owner.get('/ui/api/jobs/'+JOB+'/diagnostics/frames/0')
            self.assertEqual(frame.content,b'synthetic-jpeg')
            self.assertIn('no-store',frame.headers['cache-control'])
        with TestClient(self.app) as other:
            other.post('/ui/api/auth/login',json={'username':'other','password':'synthetic-password-42'})
            for suffix in ['/diagnostics','/diagnostics/frames/0']:
                self.assertEqual(other.get('/ui/api/jobs/'+JOB+suffix).status_code,404)
        with TestClient(self.app) as anonymous:
            self.assertEqual(anonymous.get('/ui/api/jobs/'+JOB+'/diagnostics').status_code,401)

    def test_cross_site_post_is_rejected(self):
        self.client.get('/ui/api/config')
        response=self.client.post('/ui/api/analyze',headers={'Origin':'https://other.test'},files={'file':('test.mov',b'x')})
        self.assertEqual(response.status_code,403)

    def test_oversized_upload_rejected_before_forwarding(self):
        self.client.get('/ui/api/config');self.settings.max_upload_bytes=2
        response=self.client.post('/ui/api/analyze',files={'file':('test.mov',b'too long')})
        self.assertEqual(response.status_code,413)
        self.assertNotIn('/api/v1/analyze',[path for path,_ in self.seen])

    def test_unknown_or_invalid_media_is_not_exposed(self):
        self.client.get('/ui/api/config')
        self.assertEqual(self.client.get('/ui/api/jobs/'+JOB+'/media').status_code,404)
        self.assertEqual(self.client.get('/ui/api/jobs/not-an-id').status_code,404)

    def test_upstream_failure_is_technical_error(self):
        async def broken(request):
            if request.url.path=='/readyz':return httpx.Response(200,json={})
            return httpx.Response(502,text='unavailable')
        with TestClient(create_ui(self.settings,transport=httpx.MockTransport(broken))) as client:
            client.post('/ui/api/auth/login',json={'username':'tester','password':'synthetic-password-42'})
            client.get('/ui/api/config')
            response=client.post('/ui/api/analyze',files={'file':('test.mov',b'fixture')})
            self.assertEqual(response.status_code,502)
            self.assertNotIn('no_violation',response.text)

    def test_both_design_routes_serve_same_application(self):
        dist=Path(self.tmp.name)/'dist';dist.mkdir()
        (dist/'index.html').write_text('<!doctype html><div id="root">local UI</div>')
        with TestClient(create_ui(self.settings,dist=dist)) as client:
            for route in ['/', '/v2', '/v2/']:
                response=client.get(route)
                self.assertEqual(response.status_code,200)
                self.assertIn('local UI',response.text)

    def test_only_owner_can_cancel_and_cross_site_requests_are_rejected(self):
        self.client.get('/ui/api/config')
        self.client.post('/ui/api/analyze',files={'file':('test.mov',b'fixture')})
        url='/ui/api/jobs/'+JOB+'/cancel'
        with TestClient(self.app) as other:
            other.post('/ui/api/auth/login',json={'username':'other','password':'synthetic-password-42'})
            other.get('/ui/api/config')
            self.assertEqual(other.post(url).status_code,404)
        self.assertEqual(self.client.post(url,headers={'Origin':'https://other.test'}).status_code,403)
        self.assertEqual(self.client.post(url).status_code,202)
        self.assertIn(('/api/v1/jobs/'+JOB+'/cancel','server-only-secret'),self.seen)

    def test_anonymous_access_is_blocked_and_logout_revokes_cookie(self):
        with TestClient(self.app) as anonymous:
            for url in ['/ui/api/history','/ui/api/config','/ui/api/jobs/'+JOB,'/ui/api/jobs/'+JOB+'/media']:
                self.assertEqual(anonymous.get(url).status_code,401)
            self.assertEqual(anonymous.post('/ui/api/analyze',files={'file':('test.mov',b'x')}).status_code,401)
        cookie=self.client.cookies.get('kassa_review')
        self.client.post('/ui/api/auth/logout')
        self.assertEqual(self.client.get('/ui/api/history',headers={'Cookie':'kassa_review='+cookie}).status_code,401)

    def test_relogin_from_new_browser_restores_history_without_any_analysis_request(self):
        self.client.post('/ui/api/analyze',files={'file':('original.mov',b'fixture')})
        before=len(self.seen)
        with TestClient(self.app) as fresh:
            fresh.post('/ui/api/auth/login',json={'username':'tester','password':'synthetic-password-42'})
            history=fresh.get('/ui/api/history').json()
            self.assertEqual(history['total'],1)
            self.assertEqual(history['items'][0]['filename'],'original.mov')
            self.assertEqual(fresh.get('/ui/api/jobs/'+JOB).json()['status'],'done')
        self.assertEqual(len(self.seen),before)

    def test_login_rejects_oversized_and_malformed_bodies_without_echoing_password(self):
        response=self.client.post('/ui/api/auth/login',content=b'x'*5000)
        self.assertEqual(response.status_code,413)
        response=self.client.post('/ui/api/auth/login',json={'username':'tester','password':'x'*129})
        self.assertEqual(response.status_code,400)
        self.assertNotIn('x'*129,response.text)

    def test_saved_history_and_result_work_after_gateway_restart_with_api_offline(self):
        self.client.post('/ui/api/analyze',files={'file':('offline.mov',b'fixture')})
        token=self.client.cookies.get('kassa_review')
        async def offline(request):raise httpx.ConnectError('Synthetic offline')
        with TestClient(create_ui(self.settings,transport=httpx.MockTransport(offline))) as restarted:
            restarted.cookies.set('kassa_review',token)
            self.assertFalse(restarted.get('/ui/api/config').json()['ready'])
            self.assertEqual(restarted.get('/ui/api/history').json()['total'],1)
            self.assertEqual(restarted.get('/ui/api/jobs/'+JOB).json()['status'],'done')

    def test_https_session_cookie_has_security_attributes(self):
        with TestClient(self.app,base_url='https://testserver') as secure:
            response=secure.post('/ui/api/auth/login',json={'username':'tester','password':'synthetic-password-42'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['X-Frame-Options'],'DENY')
            self.assertIn('max-age=',response.headers['Strict-Transport-Security'])
            cookie=response.headers['set-cookie']
            for attribute in ['HttpOnly','Secure','SameSite=strict','Max-Age=28800']:
                self.assertIn(attribute,cookie)
