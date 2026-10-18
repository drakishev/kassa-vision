import io
import base64
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from service.api import create_app
from service.config import Settings


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        video = Path(cls.fixture.name) / "test.mov"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=black:s=64x96:r=30:d=1", "-c:v", "libx264",
                        "-threads", "1", "-y", str(video)], check=True)
        cls.video = video.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(runtime=Path(self.temp.name), api_key="test-secret")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.headers = {"X-API-Key": "test-secret"}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def upload(self, name="test.mov", content=None):
        return self.client.post("/api/v1/analyze", headers=self.headers,
                                files={"file": (name, self.video if content is None else content)})

    def test_both_methods_require_correct_key(self):
        for headers in [{}, {"X-API-Key": "wrong"}]:
            self.assertEqual(self.client.post("/api/v1/analyze", headers=headers).status_code, 401)
            self.assertEqual(self.client.get("/api/v1/jobs/abc", headers=headers).status_code, 401)

    def test_upload_returns_job_and_processing_survives_api_restart(self):
        response = self.upload()
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        client = TestClient(create_app(self.settings))
        result = client.get(f"/api/v1/jobs/{job_id}", headers=self.headers)
        self.assertEqual(result.json(), {"job_id": job_id, "status": "processing"})
        client.close()

    def test_unknown_job_is_not_found(self):
        response = self.client.get("/api/v1/jobs/unknown", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_wrong_extension_corruption_and_empty_file_are_rejected(self):
        self.assertEqual(self.upload("test.txt").status_code, 415)
        self.assertEqual(self.upload(content=b"not-a-video").status_code, 422)
        self.assertEqual(self.upload(content=b"").status_code, 422)

    def test_filename_cannot_escape_runtime_directory(self):
        response = self.upload("../../escape.mov")
        self.assertEqual(response.status_code, 202)
        row = self.app.state.store.claim()
        self.assertTrue(Path(row["input_path"]).is_relative_to(self.settings.runtime))
        self.assertEqual(Path(row["input_path"]).name, "input.mov")

    def test_no_violation_and_failed_results_have_correct_contract(self):
        job_id = self.upload().json()["job_id"]
        self.app.state.store.claim()
        self.app.state.store.finish(job_id, {"verdict": "no_violation", "violations": []})
        response = self.client.get(f"/api/v1/jobs/{job_id}", headers=self.headers).json()
        self.assertEqual(response["status"], "done")
        self.assertEqual(response["violations"], [])
        other = self.upload().json()["job_id"]
        self.app.state.store.claim()
        self.app.state.store.fail(other, "Inference failed")
        failed = self.client.get(f"/api/v1/jobs/{other}", headers=self.headers).json()
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("verdict", failed)

    def test_positive_contract_returns_decodable_jpeg(self):
        # Contract fixture only: this does not simulate success of real model inference.
        job_id = self.upload().json()["job_id"]
        self.app.state.store.claim()
        stream = io.BytesIO()
        Image.new("RGB", (32, 32), color="black").save(stream, format="JPEG")
        self.app.state.store.finish(job_id, {"verdict": "violation", "violations": [{
            "type": "Contract fixture", "timecode_sec": 0.0, "description": "Contract fixture",
            "evidence_frames": [{"timestamp_sec": 0.0,
                                 "image_base64": base64.b64encode(stream.getvalue()).decode()}]}]})
        response = self.client.get(f"/api/v1/jobs/{job_id}", headers=self.headers).json()
        self.assertEqual(response["verdict"], "violation")
        raw = base64.b64decode(response["violations"][0]["evidence_frames"][0]["image_base64"])
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.format, "JPEG")
            image.verify()

    def test_content_length_limit_is_enforced_before_body_parsing(self):
        response = self.client.post("/api/v1/analyze", headers={**self.headers,
                     "Content-Length": str(self.settings.max_upload_bytes+2*1024*1024)}, content=b"x")
        self.assertEqual(response.status_code, 413)

    def test_chunked_upload_cannot_bypass_body_size_limit(self):
        self.settings.max_upload_bytes = 128
        boundary = b"trial-boundary"
        def body():
            yield b"--"+boundary+b'\r\nContent-Disposition: form-data; name="file"; filename="test.mov"\r\n\r\n'
            yield b"x"*(2*1024*1024)
            yield b"\r\n--"+boundary+b"--\r\n"
        response = self.client.post("/api/v1/analyze", headers={**self.headers,
            "Content-Type": "multipart/form-data; boundary=trial-boundary"}, content=body())
        self.assertEqual(response.status_code, 413)
        self.assertIn("error", response.json())

    def test_cancel_requires_key_and_persists_without_success_verdict(self):
        job_id=self.upload().json()['job_id'];url=f'/api/v1/jobs/{job_id}/cancel'
        self.assertEqual(self.client.post(url).status_code,401)
        self.assertEqual(self.client.post('/api/v1/jobs/missing/cancel',headers=self.headers).status_code,404)
        response=self.client.post(url,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['error'],'Analysis cancelled by user')
        self.assertNotIn('verdict',response.json())
        self.assertEqual(self.client.get(url.removesuffix('/cancel'),headers=self.headers).json(),response.json())
        self.assertEqual(self.client.post(url,headers=self.headers).json(),response.json())


if __name__ == "__main__":
    unittest.main()
