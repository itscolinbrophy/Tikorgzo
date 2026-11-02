import asyncio

import aiohttp

from tikorgzo.cli.text_printer import console
from tikorgzo.constants import BROWSER_USER_AGENT
from tikorgzo.core.extractors.base import BaseExtractor
from tikorgzo.core.extractors.tikwm.constants import API_REQUEST_TIMEOUT, TIKWM_API_URL
from tikorgzo.core.video import helpers as fn
from tikorgzo.core.video.model import Video
from tikorgzo.exceptions import URLParsingError, VagueErrorMessageError


class TikWMExtractor(BaseExtractor):
    """A link extractor that uses the TikWM JSON API."""

    def __init__(self, extraction_delay: float, proxy: str | None = None) -> None:
        self.proxy = proxy
        self._session: aiohttp.ClientSession | None = None
        super().__init__(extraction_delay)

    async def process_video_links(self, videos: list[Video]) -> list[Video | BaseException]:
        tasks = [self._extract(video) for video in videos]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def cleanup(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def initialize(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Referer": "https://www.tikwm.com/",
            },
            proxy="https://" + self.proxy if self.proxy else None,
        )

    async def _extract(self, video: Video) -> Video:
        async with self.semaphore:
            try:
                async with self._delay_lock:
                    if self._done_first_task:
                        await asyncio.sleep(self._extraction_delay)
                    else:
                        self._done_first_task = True

                assert self._session is not None
                async with self._session.get(
                    TIKWM_API_URL,
                    params={"url": video.video_link},
                    timeout=aiohttp.ClientTimeout(total=API_REQUEST_TIMEOUT),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()

                download_link, size, username = self._parse_payload(payload)

                if video.username is None:
                    video.username = username or "unknown"
                    fn.assign_output_paths(video)

                video.download_link = download_link
                video.file_size = size
                console.print(f"Download link retrieved for {video.video_id} (@{video.username})")
                return video
            except asyncio.CancelledError:
                console.print(f"Skipping {video.video_id} due to: [red]UserCancelledAction[/red]")
                raise
            except Exception as e:
                console.print(f"Skipping {video.video_id} due to: [red]{type(e).__name__}: {e}[/red]")
                raise

    @staticmethod
    def _parse_payload(payload: dict[str, object]) -> tuple[str, float, str | None]:
        if payload.get("code") != 0:
            message = str(payload.get("msg", "TikWM API error"))
            console.print(f"[red]TikWM API: {message}[/red]")
            lowered = message.lower()
            if "url" in lowered or "invalid" in lowered or "not found" in lowered:
                raise URLParsingError
            raise VagueErrorMessageError

        info = payload.get("data")
        if not isinstance(info, dict):
            raise VagueErrorMessageError

        download_link = info.get("hdplay") or info.get("play") or info.get("wmplay")
        if not isinstance(download_link, str) or not download_link:
            raise VagueErrorMessageError

        if info.get("hdplay"):
            size = info.get("hd_size") or info.get("size")
        elif info.get("play"):
            size = info.get("size")
        else:
            size = info.get("wm_size")

        author = info.get("author")
        username = author.get("unique_id") if isinstance(author, dict) else None
        if not isinstance(username, str) or not username:
            username = None

        return download_link, float(size or 0), username
