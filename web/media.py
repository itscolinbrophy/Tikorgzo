from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Settings

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
VIDEO_ID_RE = re.compile(r"(\d{19})")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_thumbnail_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class VideoItem:
    id: str
    name: str
    relpath: str
    username: str
    size: int
    modified: str
    video_id: str | None
    thumb_url: str
    stream_url: str
    download_url: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "relpath": self.relpath,
            "username": self.username,
            "size": self.size,
            "modified": self.modified,
            "video_id": self.video_id,
            "thumb_url": self.thumb_url,
            "stream_url": self.stream_url,
            "download_url": self.download_url,
        }


def strip_ansi(value: str) -> str:
    return CONTROL_RE.sub("", ANSI_RE.sub("", value))


def _item_id(relpath: str) -> str:
    return hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:16]


def _encode_relpath(relpath: str) -> str:
    return "/".join(quote(part) for part in relpath.split("/"))


def _to_item(path: Path, settings: Settings) -> VideoItem:
    rel = path.relative_to(settings.data_dir)
    relpath = rel.as_posix()
    item_id = _item_id(relpath)
    match = VIDEO_ID_RE.search(path.stem)
    encoded = _encode_relpath(relpath)
    base = f"/api/media/{encoded}"
    return VideoItem(
        id=item_id,
        name=path.name,
        relpath=relpath,
        username=rel.parts[0] if len(rel.parts) > 1 else "",
        size=path.stat().st_size,
        modified=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
        video_id=match.group(1) if match else None,
        thumb_url=f"/api/thumb/{encoded}",
        stream_url=base,
        download_url=f"{base}?download=1",
    )


def scan_videos(settings: Settings) -> list[VideoItem]:
    if not settings.data_dir.exists():
        return []

    items: list[VideoItem] = []
    for path in settings.data_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        rel = path.relative_to(settings.data_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        items.append(_to_item(path, settings))

    items.sort(key=lambda item: item.modified, reverse=True)
    return items


def resolve_media(settings: Settings, relpath: str) -> Path:
    root = settings.data_dir.resolve()
    candidate = (root / relpath).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise FileNotFoundError(relpath)
    if not candidate.is_file():
        raise FileNotFoundError(relpath)
    if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
        raise FileNotFoundError(relpath)
    return candidate


def _is_fresh(target: Path, source: Path) -> bool:
    return target.exists() and target.stat().st_mtime >= source.stat().st_mtime


def _is_nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


async def _run_ffmpeg(source: Path, target: Path, seek: str) -> bool:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        seek,
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale=480:-2:flags=lanczos",
        "-q:v",
        "3",
        str(target),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=45)
    except (OSError, TimeoutError):
        return False
    return process.returncode == 0 and await asyncio.to_thread(_is_nonempty_file, target)


async def ensure_thumbnail(settings: Settings, source: Path) -> Path | None:
    relpath = source.relative_to(settings.data_dir).as_posix()
    target = settings.thumb_dir / f"{_item_id(relpath)}.jpg"

    if await asyncio.to_thread(_is_fresh, target, source):
        return target

    lock = _thumbnail_locks.setdefault(relpath, asyncio.Lock())
    async with lock:
        if await asyncio.to_thread(_is_fresh, target, source):
            return target

        await asyncio.to_thread(settings.thumb_dir.mkdir, parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp.jpg")
        await asyncio.to_thread(temp.unlink, missing_ok=True)

        for seek in ("0.5", "0"):
            if await _run_ffmpeg(source, temp, seek):
                await asyncio.to_thread(temp.replace, target)
                return target

        await asyncio.to_thread(temp.unlink, missing_ok=True)
        return None
