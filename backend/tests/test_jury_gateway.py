"""Case API contract through the site origin; synthetic inputs, no GPU."""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from service.api import create_app
from service.config import Settings
from service.ui_gateway import create_ui


class JuryGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=Settings(runtime=Path(self.tmp.name),api_key='synthetic-jury-key')
        self.api=create_app(self.settings)
        self.client=TestClient(create_ui(self.settings,transport=httpx.ASGITransport(app=self.api)))
        self.headers={'X-API-Key':'synthetic-jury-key'}

    def tearDown(self):
        self.client.close();self.tmp.cleanup()

    def test_key_required_without_browser_session(self):
        for headers in ({},{'X-API-Key':'wrong-key'}):
            for path in ['/api/v1/analyze','/api/v1/jobs/'+'a'*32]:
                response=self.client.request('POST' if path.endswith('analyze') else 'GET',path,headers=headers)
                self.assertEqual(response.status_code,401)
                self.assertEqual(response.json()['error']['code'],'UNAUTHORIZED')

    def test_multipart_to_processing_then_exact_case_result(self):
        with patch('service.api.probe_video',return_value={'duration_sec':1,'width':32,'height':24,'fps':1}):
            response=self.client.post('/api/v1/analyze',headers=self.headers,
                                      files={'file':('synthetic.mov',b'synthetic-video','video/quicktime')})
        self.assertEqual(response.status_code,202)
        job_id=response.json()['job_id']
        url='/api/v1/jobs/'+job_id
        self.assertEqual(self.client.get(url,headers=self.headers).json(),{'job_id':job_id,'status':'processing'})
        self.api.state.store.claim()
        frame=io.BytesIO();Image.new('RGB',(32,24)).save(frame,format='JPEG')
        result={'verdict':'violation','violations':[{'type':'Synthetic event','timecode_sec':0,
                'description':'Synthetic contract fixture','evidence_frames':[{
                    'timestamp_sec':0,'image_base64':base64.b64encode(frame.getvalue()).decode()}]}]}
        self.api.state.store.finish(job_id,result)
        response=self.client.get(url,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),self.api.state.store.public_job(job_id))
        self.assertIn('no-store',response.headers['cache-control'])

    def test_backend_validation_and_missing_job_are_preserved(self):
        self.assertEqual(self.client.post('/api/v1/analyze',headers=self.headers).status_code,422)
        self.assertEqual(self.client.post('/api/v1/analyze',headers=self.headers,
            files={'file':('unsupported.txt',b'fixture')}).status_code,415)
        self.assertEqual(self.client.get('/api/v1/jobs/'+'a'*32,headers=self.headers).status_code,404)

    def test_chunked_upload_limit_keeps_case_error_shape(self):
        self.settings.max_upload_bytes=1
        def body():
            yield b'a'*(512*1024)
            yield b'b'*(600*1024)
        response=self.client.post('/api/v1/analyze',headers={**self.headers,'content-type':'multipart/form-data; boundary=x'},content=body())
        self.assertEqual(response.status_code,413)
        self.assertEqual(response.json()['error']['code'],'FILE_TOO_LARGE')

    def test_unavailable_upstream_is_technical_failure(self):
        async def offline(request):raise httpx.ConnectError('Synthetic offline')
        with TestClient(create_ui(self.settings,transport=httpx.MockTransport(offline))) as client:
            response=client.get('/api/v1/jobs/'+'a'*32,headers=self.headers)
        self.assertEqual(response.status_code,502)
        self.assertEqual(response.json()['error']['code'],'UPSTREAM_UNAVAILABLE')
        self.assertNotIn('no_violation',response.text)


if __name__=='__main__':unittest.main()
