from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Literal, cast

import discord
from discord.ext import commands

from bot.services.youtube import YouTubeService, YouTubeTrack


MAX_QUEUE_SIZE = 50
DEFAULT_VOLUME = 0.5
MAX_HISTORY_SIZE = 10
MAX_TRACK_RETRIES = 2
VOICE_RECONNECT_TIMEOUT = 30
VOICE_MONITOR_INTERVAL = 3
LoopMode = Literal["off", "track", "queue"]
VoiceStatus = Literal["connected", "reconnecting", "disconnected"]

FFMPEG_BEFORE_OPTIONS = (
    "-nostdin "
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_at_eof 1 "
    "-reconnect_on_network_error 1 "
    "-reconnect_delay_max 5"
)
FFMPEG_OPTIONS = "-vn"

StateChangeCallback = Callable[[int], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueuedTrack:
    track: YouTubeTrack
    requester: discord.Member


@dataclass(slots=True)
class GuildMusicState:
    queue: deque[QueuedTrack] = field(default_factory=deque)
    history: deque[QueuedTrack] = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_SIZE))
    current: QueuedTrack | None = None
    volume: float = DEFAULT_VOLUME
    loop_mode: LoopMode = "off"
    player_message_id: int | None = None
    player_channel_id: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    suppress_next_after: bool = False
    skip_requested: bool = False
    started_at: datetime | None = None
    paused_at: datetime | None = None
    paused_duration: timedelta = field(default_factory=timedelta)
    current_retries: int = 0
    last_error: str | None = None
    last_text_channel_id: int | None = None
    stopped: bool = False
    voice_status: VoiceStatus = "connected"
    reconnect_started_at: datetime | None = None
    voice_monitor_task: asyncio.Task[None] | None = None


class MusicPlayerService:
    def __init__(
        self,
        bot: commands.Bot,
        youtube: YouTubeService,
        on_state_change: StateChangeCallback,
    ) -> None:
        self.bot = bot
        self.youtube = youtube
        self.on_state_change = on_state_change
        self._states: dict[int, GuildMusicState] = {}

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    def get_voice_client(self, guild: discord.Guild) -> discord.VoiceClient | None:
        return cast(discord.VoiceClient | None, guild.voice_client)

    async def add_track(
        self,
        guild: discord.Guild,
        voice_client: discord.VoiceClient,
        track: YouTubeTrack,
        requester: discord.Member,
        text_channel_id: int | None,
    ) -> tuple[QueuedTrack, bool]:
        state = self.get_state(guild.id)
        queued_track = QueuedTrack(track=track, requester=requester)

        async with state.lock:
            if len(state.queue) >= MAX_QUEUE_SIZE:
                raise QueueFullError(MAX_QUEUE_SIZE)

            was_idle = (
                state.current is None
                and not voice_client.is_playing()
                and not voice_client.is_paused()
            )
            state.queue.append(queued_track)
            state.suppress_next_after = False
            state.skip_requested = False
            state.stopped = False
            state.voice_status = "connected"
            state.reconnect_started_at = None
            state.last_error = None
            state.last_text_channel_id = text_channel_id
            self._ensure_voice_monitor_locked(guild.id, state)

        if was_idle:
            await self.play_next(guild.id)

        return queued_track, was_idle

    async def play_next(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)

        while True:
            async with state.lock:
                if state.suppress_next_after:
                    state.suppress_next_after = False
                    state.current = None
                    state.started_at = None
                    state.paused_at = None
                    state.paused_duration = timedelta()
                    state.current_retries = 0
                    return False

                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    self._clear_current_locked(state)
                    return False

                voice_client = self.get_voice_client(guild)
                if voice_client is None or not voice_client.is_connected():
                    state.voice_status = "disconnected"
                    state.last_error = "Mat ket noi voice."
                    self._clear_current_locked(state)
                    return False

                if voice_client.is_playing() or voice_client.is_paused():
                    return False

                skip_current = state.skip_requested
                state.skip_requested = False

                if state.current is not None and not skip_current:
                    if state.loop_mode == "track":
                        queued_track = state.current
                    else:
                        state.history.appendleft(state.current)
                        if state.loop_mode == "queue":
                            state.queue.append(state.current)

                        if not state.queue:
                            self._clear_current_locked(state)
                            state.stopped = False
                            break

                        queued_track = state.queue.popleft()
                else:
                    if state.current is not None and skip_current:
                        state.history.appendleft(state.current)

                    if not state.queue:
                        self._clear_current_locked(state)
                        state.stopped = False
                        break

                    queued_track = state.queue.popleft()

                state.current = queued_track
                state.started_at = self._now()
                state.paused_at = None
                state.paused_duration = timedelta()
                state.current_retries = 0
                state.last_error = None
                state.stopped = False
                state.voice_status = "connected"
                state.reconnect_started_at = None
                volume = state.volume
                self._ensure_voice_monitor_locked(guild_id, state)

            if await self._start_current(guild_id, queued_track, volume):
                await self.on_track_start(guild_id)
                return True

            async with state.lock:
                state.history.appendleft(queued_track)
                self._clear_current_locked(state)

        await self.on_queue_end(guild_id)
        return False

    async def skip(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False

        voice_client = self.get_voice_client(guild)
        if voice_client is None or not voice_client.is_connected():
            return False

        if not voice_client.is_playing() and not voice_client.is_paused():
            return False

        state = self.get_state(guild_id)
        async with state.lock:
            state.skip_requested = True
            state.suppress_next_after = False
            state.last_error = None

        voice_client.stop()
        return True

    async def pause(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False

        voice_client = self.get_voice_client(guild)
        if voice_client is None or not voice_client.is_playing():
            return False

        voice_client.pause()
        state = self.get_state(guild_id)
        async with state.lock:
            state.paused_at = self._now()

        await self.on_state_change(guild_id)
        return True

    async def resume(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False

        voice_client = self.get_voice_client(guild)
        if voice_client is None or not voice_client.is_paused():
            return False

        voice_client.resume()
        state = self.get_state(guild_id)
        async with state.lock:
            if state.paused_at is not None:
                state.paused_duration += self._now() - state.paused_at
            state.paused_at = None

        await self.on_state_change(guild_id)
        return True

    async def toggle_pause(self, guild_id: int) -> str:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return "missing"

        voice_client = self.get_voice_client(guild)
        if voice_client is None:
            return "missing"

        if voice_client.is_playing():
            await self.pause(guild_id)
            return "paused"

        if voice_client.is_paused():
            await self.resume(guild_id)
            return "resumed"

        return "idle"

    async def stop(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        voice_client = self.get_voice_client(guild) if guild is not None else None

        async with state.lock:
            state.queue.clear()
            state.history.clear()
            state.current = None
            state.started_at = None
            state.paused_at = None
            state.paused_duration = timedelta()
            state.current_retries = 0
            state.suppress_next_after = True
            state.skip_requested = False
            state.last_error = None
            state.stopped = True
            state.voice_status = "disconnected"
            state.reconnect_started_at = None
            if state.voice_monitor_task is not None:
                state.voice_monitor_task.cancel()
                state.voice_monitor_task = None

        if voice_client is not None and (
            voice_client.is_playing() or voice_client.is_paused()
        ):
            voice_client.stop()

        await self.on_player_stopped(guild_id)

    async def leave(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False

        voice_client = self.get_voice_client(guild)
        if voice_client is None or not voice_client.is_connected():
            return False

        await self.stop(guild_id)
        await voice_client.disconnect()
        return True

    async def set_volume(self, guild_id: int, volume: float) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.volume = volume

        guild = self.bot.get_guild(guild_id)
        voice_client = self.get_voice_client(guild) if guild is not None else None
        if voice_client is not None and isinstance(
            voice_client.source,
            discord.PCMVolumeTransformer,
        ):
            voice_client.source.volume = volume

        await self.on_state_change(guild_id)

    async def set_loop(self, guild_id: int, loop_mode: LoopMode) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.loop_mode = loop_mode
        await self.on_state_change(guild_id)

    async def cycle_loop(self, guild_id: int) -> LoopMode:
        state = self.get_state(guild_id)
        order: tuple[LoopMode, ...] = ("off", "track", "queue")
        async with state.lock:
            next_index = (order.index(state.loop_mode) + 1) % len(order)
            state.loop_mode = order[next_index]
            loop_mode = state.loop_mode
        await self.on_state_change(guild_id)
        return loop_mode

    async def remove(self, guild_id: int, index: int) -> QueuedTrack | None:
        state = self.get_state(guild_id)
        async with state.lock:
            if index < 1 or index > len(state.queue):
                return None

            removed = state.queue[index - 1]
            del state.queue[index - 1]

        await self.on_state_change(guild_id)
        return removed

    async def shuffle(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        async with state.lock:
            if len(state.queue) < 2:
                return False

            items = list(state.queue)
            random.shuffle(items)
            state.queue = deque(items)

        await self.on_state_change(guild_id)
        return True

    async def clear_queue(self, guild_id: int) -> int:
        state = self.get_state(guild_id)
        async with state.lock:
            removed_count = len(state.queue)
            state.queue.clear()

        await self.on_state_change(guild_id)
        return removed_count

    def get_elapsed_seconds(self, guild_id: int) -> int:
        state = self.get_state(guild_id)
        if state.current is None or state.started_at is None:
            return 0

        elapsed = self._now() - state.started_at - state.paused_duration
        if state.paused_at is not None:
            elapsed -= self._now() - state.paused_at

        return max(0, int(elapsed.total_seconds()))

    async def handle_bot_voice_state_update(
        self,
        guild_id: int,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        state = self.get_state(guild_id)
        should_refresh = False

        async with state.lock:
            has_player_work = state.current is not None or bool(state.queue)
            if not has_player_work:
                return

            if before.channel is not None and after.channel is None:
                state.voice_status = "reconnecting"
                state.reconnect_started_at = state.reconnect_started_at or self._now()
                state.last_error = "Dang ket noi lai voice..."
                should_refresh = True
                self._ensure_voice_monitor_locked(guild_id, state)
            elif after.channel is not None:
                state.voice_status = "connected"
                state.reconnect_started_at = None
                if state.last_error in {
                    "Dang ket noi lai voice...",
                    "Mat ket noi voice.",
                }:
                    state.last_error = None
                should_refresh = True
                self._ensure_voice_monitor_locked(guild_id, state)

        if should_refresh:
            await self.on_state_change(guild_id)

    async def on_track_start(self, guild_id: int) -> None:
        await self.on_state_change(guild_id)

    async def on_track_end(self, guild_id: int) -> None:
        await self.on_state_change(guild_id)

    async def on_queue_end(self, guild_id: int) -> None:
        await self.on_state_change(guild_id)

    async def on_player_error(self, guild_id: int, error: Exception | str) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.last_error = str(error)
        await self.on_state_change(guild_id)

    async def on_player_stopped(self, guild_id: int) -> None:
        await self.on_state_change(guild_id)

    async def mark_voice_reconnecting(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            if state.current is None and not state.queue:
                return
            state.voice_status = "reconnecting"
            state.reconnect_started_at = state.reconnect_started_at or self._now()
            state.last_error = "Dang ket noi lai voice..."
            self._ensure_voice_monitor_locked(guild_id, state)

        await self.on_state_change(guild_id)

    async def cleanup_after_voice_timeout(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        voice_client = self.get_voice_client(guild) if guild is not None else None

        async with state.lock:
            state.queue.clear()
            state.history.clear()
            state.current = None
            state.started_at = None
            state.paused_at = None
            state.paused_duration = timedelta()
            state.current_retries = 0
            state.suppress_next_after = True
            state.skip_requested = False
            state.stopped = True
            state.voice_status = "disconnected"
            state.reconnect_started_at = None
            state.last_error = (
                "Mat ket noi voice qua lau. May phat nhac da tu don dep hang doi."
            )

        if voice_client is not None:
            try:
                if voice_client.is_playing() or voice_client.is_paused():
                    voice_client.stop()
                if voice_client.is_connected():
                    await voice_client.disconnect(force=True)
            except discord.DiscordException:
                logger.exception("Failed to disconnect voice client after timeout")

        await self.on_state_change(guild_id)

    async def _start_current(
        self,
        guild_id: int,
        queued_track: QueuedTrack,
        volume: float,
    ) -> bool:
        for attempt in range(MAX_TRACK_RETRIES + 1):
            refreshed_track = await self.youtube.refresh(queued_track.track)
            if refreshed_track is not None:
                queued_track.track.stream_url = refreshed_track.stream_url
                queued_track.track.thumbnail_url = refreshed_track.thumbnail_url
                queued_track.track.uploader = refreshed_track.uploader
                queued_track.track.source = refreshed_track.source

            try:
                source = self._create_audio_source(queued_track.track, volume)
                guild = self.bot.get_guild(guild_id)
                voice_client = self.get_voice_client(guild) if guild is not None else None
                if voice_client is None or not voice_client.is_connected():
                    return False

                voice_client.play(
                    source,
                    after=lambda error: self._after_play(guild_id, error),
                )
                return True
            except Exception as exc:
                logger.exception(
                    "Failed to start track '%s' on attempt %s",
                    queued_track.track.title,
                    attempt + 1,
                )
                state = self.get_state(guild_id)
                async with state.lock:
                    state.current_retries = attempt + 1
                    state.last_error = str(exc)

                if attempt >= MAX_TRACK_RETRIES:
                    await self.on_player_error(guild_id, exc)
                    return False

                await asyncio.sleep(1)

        return False

    def _create_audio_source(
        self,
        track: YouTubeTrack,
        volume: float,
    ) -> discord.PCMVolumeTransformer:
        source = discord.FFmpegPCMAudio(
            track.stream_url,
            before_options=FFMPEG_BEFORE_OPTIONS,
            options=FFMPEG_OPTIONS,
        )
        return discord.PCMVolumeTransformer(source, volume=volume)

    def _after_play(self, guild_id: int, error: Exception | None) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._handle_after_play(guild_id, error),
            self.bot.loop,
        )
        future.add_done_callback(self._log_after_error)

    async def _handle_after_play(
        self,
        guild_id: int,
        error: Exception | None,
    ) -> None:
        state = self.get_state(guild_id)

        if error is not None:
            logger.warning("Audio player error in guild %s: %s", guild_id, error)
            async with state.lock:
                should_retry = (
                    state.current is not None
                    and not state.skip_requested
                    and not state.suppress_next_after
                    and state.current_retries < MAX_TRACK_RETRIES
                )
                if should_retry:
                    state.current_retries += 1
                    queued_track = state.current
                    volume = state.volume
                    state.last_error = f"Retrying stream: {error}"
                else:
                    queued_track = None
                    volume = state.volume
                    state.last_error = str(error)

            if queued_track is not None:
                if await self._start_current(guild_id, queued_track, volume):
                    await self.on_player_error(guild_id, error)
                    return

            await self.on_player_error(guild_id, error)

        await self.on_track_end(guild_id)
        await self.play_next(guild_id)

    def _log_after_error(self, future: Future[None]) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Error while handling audio player callback")

    def _ensure_voice_monitor_locked(
        self,
        guild_id: int,
        state: GuildMusicState,
    ) -> None:
        task = state.voice_monitor_task
        if task is not None and not task.done():
            return

        state.voice_monitor_task = self.bot.loop.create_task(
            self._monitor_voice_connection(guild_id)
        )

    async def _monitor_voice_connection(self, guild_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(VOICE_MONITOR_INTERVAL)

                state = self.get_state(guild_id)
                guild = self.bot.get_guild(guild_id)
                voice_client = self.get_voice_client(guild) if guild is not None else None

                async with state.lock:
                    has_player_work = state.current is not None or bool(state.queue)
                    if not has_player_work:
                        state.voice_monitor_task = None
                        return

                    is_connected = voice_client is not None and voice_client.is_connected()
                    if is_connected:
                        if state.voice_status != "connected":
                            state.voice_status = "connected"
                            state.reconnect_started_at = None
                            if state.last_error in {
                                "Dang ket noi lai voice...",
                                "Mat ket noi voice.",
                            }:
                                state.last_error = None
                            should_refresh = True
                        else:
                            should_refresh = False
                    else:
                        if state.voice_status != "reconnecting":
                            state.voice_status = "reconnecting"
                            state.reconnect_started_at = self._now()
                            state.last_error = "Dang ket noi lai voice..."
                            should_refresh = True
                        else:
                            should_refresh = False

                        started_at = state.reconnect_started_at or self._now()
                        timed_out = (
                            self._now() - started_at
                        ).total_seconds() >= VOICE_RECONNECT_TIMEOUT

                    if is_connected:
                        timed_out = False

                if should_refresh:
                    await self.on_state_change(guild_id)

                if timed_out:
                    await self.cleanup_after_voice_timeout(guild_id)
                    return
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Voice monitor failed for guild %s", guild_id)

    def _clear_current_locked(self, state: GuildMusicState) -> None:
        state.current = None
        state.started_at = None
        state.paused_at = None
        state.paused_duration = timedelta()
        state.current_retries = 0

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


class QueueFullError(Exception):
    def __init__(self, max_size: int) -> None:
        super().__init__(f"Queue is full ({max_size} tracks)")
        self.max_size = max_size
