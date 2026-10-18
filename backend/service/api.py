"""HTTP process never imports or executes GPU models."""
import hmac
import shutil
import time
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, Security, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .jobs import JobStore, QueueFull
from .limits import UploadBodyLimit, route_path
from .schemas import APIError, DoneJob, FailedJob, ProcessingJob
from .video import probe_video


def error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(settings=None):
    settings = settings or Settings()
    store = JobStore(settings.database)
    app = FastAPI(title="Kassa Vision local video analysis", version="0.1.0")
    app.state.store = store
    app.state.settings = settings
    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    @app.middleware("http")
    async def authenticate_and_bound_upload(request, call_next):
        request.state.received_at = time.time()
        if route_path(request.scope).startswith("/api/v1/"):
            supplied = request.headers.get("X-API-Key", "")
            if not hmac.compare_digest(supplied.encode(), settings.api_key.encode()):
                return error(401, "UNAUTHORIZED", "A valid X-API-Key is required")
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) > settings.max_upload_bytes + 1024*1024:
                        return error(413, "FILE_TOO_LARGE", "Upload exceeds the configured limit")
                except ValueError:
                    return error(400, "INVALID_LENGTH", "Invalid content length")
        response = await call_next(request)
        # Multipart parsers may translate receive exceptions into HTTP 400.
        if getattr(request.state, "body_limit_exceeded", False):
            return error(413, "FILE_TOO_LARGE", "Upload exceeds the configured limit")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error(exc.status_code, "REQUEST_ERROR", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(422, "VALIDATION_ERROR", "Invalid request or missing multipart file field")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def ready():
        state = store.worker_status()
        if state["state"] not in {"ready", "busy"} or time.time()-state["heartbeat"] > 20:
            return JSONResponse(status_code=503, content={"ready": False})
        return {"ready": True}

    @app.post("/api/v1/analyze", status_code=202, response_model=ProcessingJob,
              responses={401: {"model": APIError}, 413: {"model": APIError},
                         415: {"model": APIError}, 422: {"model": APIError}, 503: {"model": APIError}})
    async def analyze(request: Request, file: UploadFile = File(...), api_key=Security(key_header)):
        if not (file.filename or "").lower().endswith(".mov"):
            await file.close()
            raise HTTPException(415, "Only .mov video files are accepted")
        job_id = uuid4().hex
        directory = settings.runtime / "jobs" / job_id
        directory.mkdir(mode=0o700)
        path = directory / "input.mov"
        accepted = False
        try:
            size = 0
            with path.open("xb") as destination:
                while chunk := await file.read(1024*1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "Upload exceeds the configured limit")
                    destination.write(chunk)
            try:
                metadata = await run_in_threadpool(probe_video, path, settings.max_duration_sec)
                metadata['received_at'] = request.state.received_at
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            try:
                store.enqueue(job_id, str(path), metadata, settings.max_pending)
            except QueueFull as exc:
                raise HTTPException(503,
                                    "Analysis queue is full; retry later") from exc
            accepted = True
            return ProcessingJob(job_id=job_id)
        finally:
            await file.close()
            if not accepted:
                shutil.rmtree(directory)

    @app.get("/api/v1/jobs/{job_id}", response_model=ProcessingJob | DoneJob | FailedJob,
             responses={401: {"model": APIError}, 404: {"model": APIError}})
    def job(job_id: str, api_key=Security(key_header)):
        result = store.public_job(job_id)
        if result is None:
            raise HTTPException(404, "Job not found")
        return result

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=DoneJob | FailedJob,
              responses={401: {"model": APIError}, 404: {"model": APIError}})
    def cancel(job_id: str, api_key=Security(key_header)):
        result=store.cancel(job_id)
        if result is None:
            raise HTTPException(404, "Job not found")
        return result

    @app.get('/api/v1/jobs/{job_id}/diagnostics')
    def diagnostics(job_id: str, api_key=Security(key_header)):
        from .diagnostics import load_diagnostics, projection
        _,data=load_diagnostics(settings,store,job_id)
        return projection(job_id,data)

    @app.get('/api/v1/jobs/{job_id}/diagnostics/frames/{frame_index}')
    def diagnostic_frame(job_id: str, frame_index: int, api_key=Security(key_header)):
        from .diagnostics import load_diagnostics, saved_frame
        run,data=load_diagnostics(settings,store,job_id)
        return FileResponse(saved_frame(run,data,frame_index),media_type='image/jpeg',
                            headers={'Cache-Control':'private, no-store'})

    app.add_middleware(UploadBodyLimit, settings=settings)
    return app
