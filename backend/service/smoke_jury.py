"""Check the real API and saved diagnostics using generated, non-bank video."""
import argparse
import base64
import io
import json
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image

from .config import Settings
from .schemas import DoneJob


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18863')
    parser.add_argument('--internal-url', default='http://127.0.0.1:18861')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    settings = Settings()
    with sqlite3.connect(f'file:{settings.database}?mode=ro', uri=True) as db:
        pending = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
    if pending:
        raise SystemExit('Wait for the analysis queue to become idle')
    headers = {'X-API-Key': settings.api_key}
    report = {'synthetic_only': True, 'quality_metrics_claimed': False}
    output = args.output or settings.runtime / 'smoke.json'
    job_id = None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='kassa-smoke-') as temp, httpx.Client(trust_env=False, timeout=20) as client:
        url = args.url.rstrip('/')
        internal = args.internal_url.rstrip('/')
        for key in ('', 'invalid-synthetic-key'):
            response = client.post(url + '/api/v1/analyze', headers={'X-API-Key': key})
            assert response.status_code == 401
        report['missing_and_invalid_key_rejected'] = True
        source = Path(temp) / 'synthetic.mov'
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'color=c=gray:size=320x240:rate=4:duration=1', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(source)], check=True, timeout=20)
        with source.open('rb') as stream:
            response = client.post(url + '/api/v1/analyze', headers=headers,
                                   files={'file': (source.name, stream, 'video/quicktime')})
        assert response.status_code == 202
        job_id = response.json()['job_id']
        report['upload_status'] = 202
        print(json.dumps({'stage': 'synthetic_submitted', 'http_status': 202}), flush=True)
        try:
            while time.monotonic() - started < 300:
                response = client.get(url + '/api/v1/jobs/' + job_id, headers=headers)
                assert response.status_code == 200
                result = response.json()
                if result['status'] != 'processing':
                    break
                time.sleep(2)
            assert result['status'] == 'done', 'Synthetic analysis did not complete'
            validated = DoneJob.model_validate(result)
            report['status'] = validated.status
            report['violation_count'] = len(validated.violations)
            for event in validated.violations:
                for frame in event.evidence_frames:
                    with Image.open(io.BytesIO(base64.b64decode(frame.image_base64))) as image:
                        assert image.format == 'JPEG'
                        image.verify()
            endpoint = internal + '/api/v1/jobs/' + job_id + '/diagnostics'
            response = client.get(endpoint, headers=headers)
            assert response.status_code == 200
            diagnostics = response.json()
            assert diagnostics['pipeline'] == 'legacy-supported'
            report['pipeline'] = diagnostics['pipeline']
            report['frame_count'] = len(diagnostics['frames'])
            report['available_layers'] = sum(v == 'available' for v in diagnostics['capabilities'].values())
            assert report['available_layers'] == 2
            response = client.get(endpoint + '/frames/0', headers=headers)
            assert response.status_code == 200
            with Image.open(io.BytesIO(response.content)) as image:
                assert image.size == (diagnostics['frames'][0]['width'], diagnostics['frames'][0]['height'])
                image.verify()
            report.update(saved_frame_verified=True, case_schema_valid=True)
        except Exception:
            client.post(internal + '/api/v1/jobs/' + job_id + '/cancel', headers=headers)
            raise
        finally:
            report['elapsed_sec'] = round(time.monotonic() - started, 3)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
