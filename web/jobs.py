from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiohttp

from tikorgzo.constants import BROWSER_USER_AGENT
from tikorgzo.core.extractors.tikwm.constants import TIKWM_API_URL

from .media import strip_ansi

if TYPE_CHECKING:
    from .config import Settings

MAX_JOBS = 100
SHORT_LINK_RE = re.compile(r"https?://(?:vt|vm)\.tiktok\.com/", re.IGNORECASE)

STAGE_PATTERNS = (
    ("Stage 1/3", "Validating link"),
    ("Stage 2/3", "Extracting high-quality link"),
    ("Stage 3/3", "Downloading"),
    ("Download link retrieved", "Downloading"),
    ("Finished downloads", "Finishing up"),
)

ERROR_HINTS = ("error", "failed", "invalid", "not installed", "missing", "timeout", "exception", "skipping")


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass
class Job:
    id: str
    url: str
    status: str = "queued"
    stage: str = "Queued"
    message: str = ""
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    log: str = ""

    def to_dict(self, *, include_log: bool = False) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "returncode": self.returncode,
        }
        if include_log:
            data["log"] = self.log
        return data


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrent)
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run_worker())

    async def stop(self) -> None:
        for process in list(self._processes.values()):
            self._signal(process, signal.SIGTERM)
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        for process in list(self._processes.values()):
            self._signal(process, signal.SIGKILL)

    async def create(self, url: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url)
        self.jobs[job.id] = job
        self._prune()
        await self.queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)[:50]

    def _prune(self) -> None:
        if len(self.jobs) <= MAX_JOBS:
            return
        finished = [job for job in self.jobs.values() if job.status not in {"queued", "running"}]
        finished.sort(key=lambda job: job.created_at)
        for job in finished[: max(0, len(self.jobs) - MAX_JOBS)]:
            self.jobs.pop(job.id, None)

    async def _run_worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if job is None:
                continue
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                job.status = "error"
                job.stage = "Stopped"
                job.message = "Server is shutting down"
                raise
            except Exception as exc:
                job.status = "error"
                job.stage = "Failed"
                job.message = f"{type(exc).__name__}: {exc}"
                job.finished_at = _now()
            finally:
                self.queue.task_done()

    def _command(self, url: str) -> list[str]:
        command: list[str] = []
        command.extend(
            [
                self.settings.tikorgzo_bin,
                "-l",
                url,
                "--download-dir",
                str(self.settings.data_dir),
                "--extractor",
                self.settings.extractor,
            ],
        )
        command.extend(self.settings.extra_args)
        return command

    async def _resolve_short_url(self, url: str) -> str:
        if not SHORT_LINK_RE.search(url):
            return url
        try:
            async with (
                aiohttp.ClientSession(
                    headers={
                        "User-Agent": BROWSER_USER_AGENT,
                        "Referer": "https://www.tikwm.com/",
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as session,
                session.get(TIKWM_API_URL, params={"url": url}) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return url

        data = payload.get("data") if isinstance(payload, dict) else None
        video_id = data.get("id") if isinstance(data, dict) else None
        if not video_id:
            return url

        await asyncio.sleep(1.2)
        return str(video_id)

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("NO_COLOR", "1")
        env.setdefault("TERM", "dumb")
        env.setdefault("COLUMNS", "120")
        return env

    async def _execute(self, job: Job) -> None:
        async with self._semaphore:
            job.status = "running"
            job.stage = "Resolving link"
            job.started_at = _now()
            job.message = ""

            resolved = await self._resolve_short_url(job.url)
            if resolved != job.url:
                job.url = resolved
            job.stage = "Starting"

            self.settings.job_log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.settings.job_log_dir / f"{job.id}.log"
            command = self._command(job.url)

            with log_path.open("wb") as log_file:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                    cwd=str(self.settings.data_dir),
                    env=self._environment(),
                    start_new_session=True,
                )
                self._processes[job.id] = process
                try:
                    assert process.stdout is not None
                    async for raw in process.stdout:
                        log_file.write(raw)
                        log_file.flush()
                        line = raw.decode("utf-8", "replace").replace("\r", "\n")
                        line = strip_ansi(line)
                        job.log = (job.log + line)[-20000:]
                        self._update_stage(job, line)
                    job.returncode = await process.wait()
                finally:
                    self._processes.pop(job.id, None)

            job.finished_at = _now()
            if job.returncode == 0:
                job.status = "done"
                job.stage = "Finished"
                job.message = self._success_message(job.log)
            else:
                job.status = "error"
                job.stage = "Failed"
                job.message = self._error_message(job.log, job.returncode)

    @staticmethod
    def _update_stage(job: Job, line: str) -> None:
        text = line.strip()
        if not text:
            return
        if "already exists" in text.lower():
            job.stage = "Already downloaded"
            return
        for marker, stage in STAGE_PATTERNS:
            if marker.lower() in text.lower():
                job.stage = stage
                return
        if text.lower().startswith("downloading "):
            job.stage = "Downloading"

    @staticmethod
    def _success_message(log: str) -> str:
        lowered = log.lower()
        if "already exists" in lowered or "nothing to process" in lowered:
            return "Already downloaded"
        return "Download complete"

    @staticmethod
    def _error_message(log: str, returncode: int | None) -> str:
        lines = [line.strip() for line in log.splitlines() if line.strip()]
        for line in reversed(lines):
            lowered = line.lower()
            if any(hint in lowered for hint in ERROR_HINTS):
                return re.sub(r"\s+", " ", line)[:500]
        if lines:
            return re.sub(r"\s+", " ", lines[-1])[:500]
        return f"tikorgzo exited with code {returncode}"

    @staticmethod
    def _signal(process: asyncio.subprocess.Process, sig: int) -> None:
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(sig)
