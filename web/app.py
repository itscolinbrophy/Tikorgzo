from __future__ import annotations

import asyncio
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from .config import settings
from .jobs import JobManager
from .media import delete_video, ensure_thumbnail, resolve_media, scan_videos

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

TIKTOK_HOST_RE = re.compile(r"^(https?://)?([a-z0-9-]+\.)*tiktok\.com/", re.IGNORECASE)
VIDEO_ID_RE = re.compile(r"^\d{19}$")
STATIC_DIR = Path(__file__).parent / "static"
MAX_URL_LENGTH = 2048
EMPTY_URL_MESSAGE = "Paste a TikTok link first."
LONG_URL_MESSAGE = "That link is too long."
INVALID_URL_MESSAGE = "Enter a TikTok link or a 19-digit video ID."

jobs = JobManager(settings)


class InvalidDownloadRequestError(ValueError):
    pass


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    for directory in (settings.data_dir, settings.thumb_dir, settings.job_log_dir):
        directory.mkdir(parents=True, exist_ok=True)
    jobs.start()
    try:
        yield
    finally:
        await jobs.stop()


app = FastAPI(title="Tikorgzo Web", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class DownloadRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = value.strip()
        if not url:
            raise InvalidDownloadRequestError(EMPTY_URL_MESSAGE)
        if len(url) > MAX_URL_LENGTH:
            raise InvalidDownloadRequestError(LONG_URL_MESSAGE)
        if VIDEO_ID_RE.fullmatch(url):
            return url
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        if not TIKTOK_HOST_RE.match(url):
            raise InvalidDownloadRequestError(INVALID_URL_MESSAGE)
        return url


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "data_dir": str(settings.data_dir),
        "tikorgzo": settings.tikorgzo_bin,
    }


@app.get("/api/videos")
async def videos() -> dict[str, object]:
    items = await asyncio.to_thread(scan_videos, settings)
    return {
        "items": [item.to_dict() for item in items],
        "count": len(items),
        "total_bytes": sum(item.size for item in items),
    }


@app.post("/api/download", status_code=202)
async def download(request: DownloadRequest) -> dict[str, object]:
    job = await jobs.create(request.url)
    return job.to_dict()


@app.get("/api/jobs")
async def list_jobs() -> dict[str, object]:
    return {"jobs": [job.to_dict() for job in jobs.list()]}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, object]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict(include_log=True)


@app.get("/api/thumb/{relpath:path}")
async def thumbnail(relpath: str) -> FileResponse:
    try:
        source = await asyncio.to_thread(resolve_media, settings, relpath)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Video not found") from None
    target = await ensure_thumbnail(settings, source)
    if target is None:
        raise HTTPException(status_code=404, detail="Thumbnail unavailable")
    return FileResponse(target, media_type="image/jpeg")


@app.get("/api/media/{relpath:path}")
async def media(relpath: str, download: Annotated[bool, Query()] = False) -> FileResponse:
    try:
        source = await asyncio.to_thread(resolve_media, settings, relpath)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Video not found") from None

    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    safe_name = source.name.replace('"', "").replace("\r", "").replace("\n", "")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        source,
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{safe_name}"'},
    )


@app.delete("/api/media/{relpath:path}")
async def delete_media(relpath: str) -> dict[str, str]:
    try:
        deleted = await asyncio.to_thread(delete_video, settings, relpath)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Video not found") from None
    return {"deleted": deleted}
