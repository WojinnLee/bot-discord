import asyncio
from dataclasses import dataclass
from typing import Any

import yt_dlp


@dataclass(slots=True)
class YouTubeTrack:
    title: str
    webpage_url: str
    stream_url: str
    duration: int | None


class YouTubeService:
    def __init__(self) -> None:
        self._ydl_options: dict[str, Any] = {
            "format": "bestaudio[acodec=opus]/bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "default_search": "ytsearch1",
            "extract_flat": False,
            "retries": 3,
            "socket_timeout": 15,
            "source_address": "0.0.0.0",
        }

    async def search(self, query: str) -> YouTubeTrack | None:
        query = query.strip()
        if not query:
            return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._extract_track, query)

    async def refresh(self, track: YouTubeTrack) -> YouTubeTrack | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._extract_track, track.webpage_url)

    def _extract_track(self, query: str) -> YouTubeTrack | None:
        try:
            with yt_dlp.YoutubeDL(self._ydl_options) as ydl:
                info = ydl.extract_info(query, download=False)
        except yt_dlp.utils.DownloadError:
            return None
        except Exception:
            return None

        if not info:
            return None

        if "entries" in info:
            entries = info.get("entries") or []
            info = next((entry for entry in entries if entry), None)

        if not info:
            return None

        stream_url = info.get("url")
        title = info.get("title")
        webpage_url = info.get("webpage_url") or info.get("original_url")

        if not stream_url or not title or not webpage_url:
            return None

        return YouTubeTrack(
            title=title,
            webpage_url=webpage_url,
            stream_url=stream_url,
            duration=info.get("duration"),
        )
