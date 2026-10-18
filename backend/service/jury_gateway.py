"""Expose the case API on the website origin, using the caller's API key."""
import hmac
import re

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException


def api_error(status, code, message):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


def register_jury_api(app, settings, api_url, transport=None):
    async def forward(request, path):
        key = request.headers.get('x-api-key', '')
        if not hmac.compare_digest(key.encode(), settings.api_key.encode()):
            return api_error(401, 'UNAUTHORIZED', 'A valid X-API-Key is required')
        headers = {'X-API-Key': key}
        for name in ('content-type', 'content-length'):
            if name in request.headers:
                headers[name] = request.headers[name]
        try:
            async with httpx.AsyncClient(base_url=api_url, transport=transport,
                                         trust_env=False, timeout=httpx.Timeout(120, connect=5)) as client:
                response = await client.request(request.method, path, headers=headers,
                                                content=request.stream() if request.method == 'POST' else None)
            if response.headers.get('content-type', '').split(';')[0] != 'application/json':
                return api_error(502, 'UPSTREAM_UNAVAILABLE', 'Analysis API returned an invalid response')
            return Response(response.content, status_code=response.status_code, media_type='application/json')
        except HTTPException as exc:
            return api_error(exc.status_code, 'FILE_TOO_LARGE', 'Upload exceeds the configured limit')
        except httpx.HTTPError:
            return api_error(502, 'UPSTREAM_UNAVAILABLE', 'Analysis API is temporarily unavailable')

    @app.post('/api/v1/analyze')
    async def analyze(request: Request):
        return await forward(request, '/api/v1/analyze')

    @app.get('/api/v1/jobs/{job_id}')
    async def job(request: Request, job_id: str):
        # Prevent path normalization from reaching another upstream resource.
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            return api_error(404, 'REQUEST_ERROR', 'Job not found')
        return await forward(request, '/api/v1/jobs/' + job_id)
