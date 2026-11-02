from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 16) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    data_dir: Path
    thumb_dir: Path
    job_log_dir: Path
    tikorgzo_bin: str
    extractor: str
    max_concurrent: int
    extra_args: tuple[str, ...]
    host: str
    port: int


def _build_settings() -> Settings:
    data_dir = Path(os.environ.get("TIKORGZO_DATA_DIR", "data/downloads")).expanduser().resolve()
    base_dir = data_dir.parent

    def _dir(name: str, fallback: Path) -> Path:
        raw = os.environ.get(name)
        if raw:
            return Path(raw).expanduser().resolve()
        return fallback

    return Settings(
        base_dir=base_dir,
        data_dir=data_dir,
        thumb_dir=_dir("TIKORGZO_THUMB_DIR", base_dir / ".thumbnails"),
        job_log_dir=_dir("TIKORGZO_JOB_LOG_DIR", base_dir / ".jobs"),
        tikorgzo_bin=os.environ.get("TIKORGZO_BIN") or shutil.which("tikorgzo") or "tikorgzo",
        extractor=os.environ.get("TIKORGZO_EXTRACTOR", "tikwm").strip() or "tikwm",
        max_concurrent=_env_int("TIKORGZO_MAX_CONCURRENT", 1, 1, 8),
        extra_args=tuple(shlex.split(os.environ.get("TIKORGZO_EXTRA_ARGS", ""))),
        host=os.environ.get("TIKORGZO_HOST", "0.0.0.0"),
        port=_env_int("TIKORGZO_PORT", 8080, 1, 65535),
    )


settings = _build_settings()
