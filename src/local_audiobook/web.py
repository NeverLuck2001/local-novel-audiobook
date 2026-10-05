"""Loopback-only HTTP interface for the offline audiobook queue."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit
import uuid

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .downloads import stream_zip
from .jobs import BOOK_SUFFIXES, JobManager, REFERENCE_SUFFIXES
from .util import safe_name
from .voices import MAX_REFERENCE_UPLOAD_BYTES, MAX_VIDEO_UPLOAD_BYTES, VIDEO_SUFFIXES

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
PICKER_LOCK = threading.Lock()
DownloadId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
DownloadFormat = Literal["flac", "m4b", "mp3", "wav"]


class Selection(BaseModel):
    files: list[str] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)


class JobRequest(Selection):
    kind: str = "convert"
    preview_text: str | None = None


class ReferenceClip(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    start: float = Field(default=0, ge=0, le=600, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, le=600, allow_inf_nan=False)
    text: str = Field(default="", max_length=3000)


class ReferenceRequest(BaseModel):
    clips: list[ReferenceClip] = Field(min_length=1, max_length=5)


class VoiceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    reference_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=12000)


class DownloadRequest(BaseModel):
    jobs: list[DownloadId] = Field(min_length=1, max_length=100)
    format: DownloadFormat = "flac"


class RecordRequest(BaseModel):
    ids: list[DownloadId] = Field(min_length=1, max_length=200)
    action: Literal["archive", "unarchive"] = "archive"


class BulkJobRequest(BaseModel):
    ids: list[DownloadId] = Field(min_length=1, max_length=200)
    action: Literal["remove", "unarchive", "cancel"] = "remove"


def create_app(app_root: Path | None = None, workspace: Path | None = None) -> FastAPI:
    root = (app_root or Path(__file__).resolve().parents[2]).resolve()
    manager = JobManager(root, workspace)

    @asynccontextmanager
    async def lifespan(_app):
        manager.start()
        try:
            yield
        finally:
            manager.close()

    app = FastAPI(title="Local Novel Audiobook", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.manager = manager

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "This interface accepts loopback hosts only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                allowed = (parsed.scheme in {"http", "https"}
                           and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                           and parsed.port == request.url.port)
            except ValueError:
                allowed = False
            if not allowed:
                return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing_value(_request, _exc):
        return JSONResponse({"detail": "The requested file or job does not exist"}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        return JSONResponse({"detail": "; ".join(item["msg"] for item in exc.errors())}, status_code=422)

    @app.get("/api/info")
    def info():
        return manager.info()

    @app.get("/api/files")
    def files(include_archived: bool = False):
        return {"files": manager.list_files(include_archived)}

    @app.post("/api/files/bulk")
    def archive_files(selection: RecordRequest):
        return manager.archive_records("files", selection.ids, selection.action == "archive")

    async def save_upload(file: UploadFile, reference=False):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (REFERENCE_SUFFIXES if reference else BOOK_SUFFIXES):
            raise ValueError("Upload TXT/EPUB books or a supported reference audio/video file")
        uploads = manager.root / "uploads"
        uploads.mkdir(exist_ok=True)
        target = uploads / (uuid.uuid4().hex + "-" + safe_name(Path(file.filename).stem, 80) + suffix)
        total = 0
        video = reference and suffix in VIDEO_SUFFIXES
        limit = MAX_VIDEO_UPLOAD_BYTES if video else MAX_REFERENCE_UPLOAD_BYTES if reference else MAX_UPLOAD_BYTES
        try:
            with target.open("wb") as stream:
                while block := await file.read(1024 * 1024):
                    total += len(block)
                    if total > limit:
                        raise ValueError("A reference video exceeds the 512 MB limit" if video else
                                         "A reference recording exceeds the 64 MB limit" if reference else
                                         "An uploaded file exceeds the 512 MB limit")
                    stream.write(block)
            return await run_in_threadpool(manager.register_upload, target, Path(file.filename).name,
                                           reference=reference)
        except Exception:
            target.unlink(missing_ok=True)
            if video:
                target.with_name(target.stem + "-audio.wav").unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post("/api/files/upload")
    async def upload(files: Annotated[list[UploadFile], File()]):
        if len(files) > 100:
            raise ValueError("Upload at most 100 files at a time")
        return {"files": [await save_upload(file) for file in files]}

    @app.post("/api/files/reference")
    async def reference(file: Annotated[UploadFile, File()]):
        return await save_upload(file, reference=True)

    @app.post("/api/references/prepare")
    def prepare_reference(selection: ReferenceRequest):
        return manager.create_reference([clip.model_dump() for clip in selection.clips])

    @app.get("/api/references/{identifier}/audio")
    def reference_audio(identifier: str):
        item = manager.reference_file(identifier)
        filename = Path(item["name"]).with_suffix(Path(item["path"]).suffix).name
        return FileResponse(item["path"], filename=filename)

    @app.get("/api/voices")
    def voices(include_archived: bool = False):
        return {"voices": manager.list_voices(include_archived)}

    @app.post("/api/voices/bulk")
    def archive_voices(selection: RecordRequest):
        return manager.archive_records("voices", selection.ids, selection.action == "archive")

    @app.post("/api/voices")
    def save_voice(selection: VoiceRequest):
        return manager.save_voice(selection.name, selection.reference_id, selection.text)

    @app.post("/api/voices/{identifier}/archive")
    def archive_voice(identifier: str):
        return manager.archive_voice(identifier)

    @app.post("/api/files/pick")
    def pick():
        if not PICKER_LOCK.acquire(blocking=False):
            raise HTTPException(409, "A file picker is already open")
        window = None
        try:
            import tkinter
            from tkinter.filedialog import askopenfilenames
            window = tkinter.Tk()
            window.withdraw()
            window.attributes("-topmost", True)
            selected = askopenfilenames(parent=window, title="Select TXT or EPUB books",
                                       initialdir=str(manager.workspace / "books"),
                                       filetypes=[("Novel files", "*.txt *.epub")])
            return {"files": [manager.register_file(Path(path)) for path in selected]}
        except Exception as exc:
            raise HTTPException(503, "Native picker is unavailable; use browser upload instead") from exc
        finally:
            if window is not None:
                window.destroy()
            PICKER_LOCK.release()

    @app.post("/api/plan")
    def plan(selection: Selection):
        return manager.plan(selection.files, selection.settings)

    @app.post("/api/jobs")
    def create_job(selection: JobRequest):
        return manager.create(selection.files, selection.settings, selection.kind, selection.preview_text)

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": manager.list_jobs()}

    @app.post("/api/jobs/bulk")
    def bulk_jobs(selection: BulkJobRequest):
        return manager.bulk_jobs(selection.ids, selection.action)

    @app.post("/api/queue/cancel")
    def cancel_queued():
        return manager.cancel_queued()

    @app.get("/api/jobs/{identifier}")
    def detail(identifier: str):
        return manager.detail(identifier)

    @app.post("/api/jobs/{identifier}/{action}")
    def action(identifier: str, action: str):
        return manager.action(identifier, action)

    @app.get("/api/monitor")
    def monitor(include_removed: bool = False):
        return manager.monitor(include_removed)

    @app.post("/api/monitor/{identifier}/{action}")
    def monitor_action(identifier: str, action: str):
        return manager.monitor_action(identifier, action)

    @app.get("/api/media/{identifier}/{file_id}")
    def media(identifier: str, file_id: str):
        path = manager.media_path(identifier, file_id)
        media_type = {".m4b": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
                      ".flac": "audio/flac"}[path.suffix.lower()]
        return FileResponse(path, filename=path.name, media_type=media_type)

    @app.post("/api/downloads")
    def prepare_download(selection: DownloadRequest):
        records = manager.download_records(selection.jobs, selection.format)
        identifiers = list(dict.fromkeys(selection.jobs))
        query = urlencode([("format", selection.format), *[("job", item) for item in identifiers]])
        return {"url": "/api/downloads?" + query, "jobs": len(identifiers),
                "files": len(records), "size": sum(record["size"] for record in records)}

    @app.get("/api/downloads")
    def download(job: Annotated[list[DownloadId], Query(min_length=1, max_length=100)],
                 format: DownloadFormat = "flac"):
        records = manager.download_records(job, format)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"audiobooks-{format}-{stamp}.zip"
        return StreamingResponse(stream_zip(records), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"',
                                          "Cache-Control": "no-store"})

    static = Path(__file__).with_name("web")
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    else:
        @app.get("/")
        def pending_frontend():
            return JSONResponse({"detail": "The local frontend files are missing"}, status_code=503)
    return app
