import asyncio
from dataclasses import replace
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import yt_dlp


@dataclass(slots=True)
class YouTubeTrack:
    title: str
    webpage_url: str
    stream_url: str
    duration: int | None
    thumbnail_url: str | None
    uploader: str | None
    source: str


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
        self._search_cache: dict[str, tuple[YouTubeTrack, datetime]] = {}
        self._search_cache_ttl = timedelta(minutes=10)
        self._search_cache_max_size = 100

    async def search(self, query: str) -> YouTubeTrack | None:
        query = query.strip()
        if not query:
            return None

        cached_track = self._get_cached_search(query)
        if cached_track is not None:
            return cached_track

        loop = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, self._extract_track, query)
        if track is not None:
            self._set_cached_search(query, track)

        return track

    async def refresh(self, track: YouTubeTrack) -> YouTubeTrack | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._extract_track, track.webpage_url)

    def clear_expired_cache(self) -> None:
        now = self._now()
        expired_keys = [
            key
            for key, (_, cached_at) in self._search_cache.items()
            if now - cached_at >= self._search_cache_ttl
        ]
        for key in expired_keys:
            del self._search_cache[key]

    def _get_cached_search(self, query: str) -> YouTubeTrack | None:
        self.clear_expired_cache()
        cache_key = self._cache_key(query)
        cached = self._search_cache.get(cache_key)
        if cached is None:
            return None

        track, cached_at = cached
        if self._now() - cached_at >= self._search_cache_ttl:
            del self._search_cache[cache_key]
            return None

        return replace(track)

    def _set_cached_search(self, query: str, track: YouTubeTrack) -> None:
        self.clear_expired_cache()
        if len(self._search_cache) >= self._search_cache_max_size:
            oldest_key = min(
                self._search_cache,
                key=lambda key: self._search_cache[key][1],
            )
            del self._search_cache[oldest_key]

        self._search_cache[self._cache_key(query)] = (replace(track), self._now())

    def _cache_key(self, query: str) -> str:
        return query.strip().lower()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

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
            thumbnail_url=info.get("thumbnail"),
            uploader=info.get("uploader") or info.get("channel"),
            source=info.get("extractor_key") or "YouTube",
        )
