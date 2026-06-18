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
PLAYBACK_WATCHDOG_INTERVAL = 5
PLAYBACK_STUCK_TIMEOUT = 15
CLEANUP_INTERVAL = 60
IDLE_CLEANUP_TIMEOUT = 300
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
    preload_task: asyncio.Task[None] | None = None
    preloaded_track_key: str | None = None
    preloaded_track: YouTubeTrack | None = None
    preload_error: str | None = None
    playback_watchdog_task: asyncio.Task[None] | None = None
    last_progress_seconds: int = 0
    last_progress_checked_at: datetime | None = None
    stuck_count: int = 0
    consecutive_failures: int = 0
    recovery_in_progress: bool = False
    not_playing_since: datetime | None = None
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
        self.loop = asyncio.get_running_loop()
        self._states: dict[int, GuildMusicState] = {}
        self._cleanup_task: asyncio.Task[None] | None = self.loop.create_task(
            self._cleanup_idle_states()
        )

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    def get_voice_client(self, guild: discord.Guild) -> discord.VoiceClient | None:
        return cast(discord.VoiceClient | None, guild.voice_client)

    def available_queue_slots(self, guild_id: int) -> int:
        state = self.get_state(guild_id)
        return max(0, MAX_QUEUE_SIZE - len(state.queue))

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
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)
            self._ensure_voice_monitor_locked(guild.id, state)

        if was_idle:
            await self.play_next(guild.id)
        else:
            await self._preload_next_track(guild.id)

        return queued_track, was_idle

    async def insert_next(
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
            if was_idle:
                state.queue.append(queued_track)
            else:
                state.queue.appendleft(queued_track)

            state.suppress_next_after = False
            state.skip_requested = False
            state.stopped = False
            state.voice_status = "connected"
            state.reconnect_started_at = None
            state.last_error = None
            state.last_text_channel_id = text_channel_id
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)
            self._ensure_voice_monitor_locked(guild.id, state)

        if was_idle:
            await self.play_next(guild.id)
        else:
            await self._preload_next_track(guild.id)
            await self.on_state_change(guild.id)

        return queued_track, was_idle

    async def add_tracks(
        self,
        guild: discord.Guild,
        voice_client: discord.VoiceClient,
        tracks: list[YouTubeTrack],
        requester: discord.Member,
        text_channel_id: int | None,
    ) -> tuple[int, bool]:
        state = self.get_state(guild.id)
        if not tracks:
            return 0, False

        queued_tracks = [
            QueuedTrack(track=track, requester=requester)
            for track in tracks
        ]

        async with state.lock:
            slots = MAX_QUEUE_SIZE - len(state.queue)
            if slots <= 0:
                raise QueueFullError(MAX_QUEUE_SIZE)

            queued_tracks = queued_tracks[:slots]
            was_idle = (
                state.current is None
                and not voice_client.is_playing()
                and not voice_client.is_paused()
            )
            state.queue.extend(queued_tracks)
            state.suppress_next_after = False
            state.skip_requested = False
            state.stopped = False
            state.voice_status = "connected"
            state.reconnect_started_at = None
            state.last_error = None
            state.last_text_channel_id = text_channel_id
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)
            self._ensure_voice_monitor_locked(guild.id, state)
            added_count = len(queued_tracks)

        if was_idle:
            await self.play_next(guild.id)
        else:
            await self._preload_next_track(guild.id)
            await self.on_state_change(guild.id)

        return added_count, was_idle

    async def insert_tracks_next(
        self,
        guild: discord.Guild,
        voice_client: discord.VoiceClient,
        tracks: list[YouTubeTrack],
        requester: discord.Member,
        text_channel_id: int | None,
    ) -> tuple[int, bool]:
        state = self.get_state(guild.id)
        if not tracks:
            return 0, False

        queued_tracks = [
            QueuedTrack(track=track, requester=requester)
            for track in tracks
        ]

        async with state.lock:
            slots = MAX_QUEUE_SIZE - len(state.queue)
            if slots <= 0:
                raise QueueFullError(MAX_QUEUE_SIZE)

            queued_tracks = queued_tracks[:slots]
            was_idle = (
                state.current is None
                and not voice_client.is_playing()
                and not voice_client.is_paused()
            )
            if was_idle:
                state.queue.extend(queued_tracks)
            else:
                for queued_track in reversed(queued_tracks):
                    state.queue.appendleft(queued_track)

            state.suppress_next_after = False
            state.skip_requested = False
            state.stopped = False
            state.voice_status = "connected"
            state.reconnect_started_at = None
            state.last_error = None
            state.last_text_channel_id = text_channel_id
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)
            self._ensure_voice_monitor_locked(guild.id, state)
            added_count = len(queued_tracks)

        if was_idle:
            await self.play_next(guild.id)
        else:
            await self._preload_next_track(guild.id)
            await self.on_state_change(guild.id)

        return added_count, was_idle

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
                    self._cancel_playback_watchdog_locked(state)
                    self._invalidate_preload_locked(state)
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
                    self._cancel_playback_watchdog_locked(state)
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
                            self._cancel_playback_watchdog_locked(state)
                            break

                        queued_track = state.queue.popleft()
                else:
                    if state.current is not None and skip_current:
                        state.history.appendleft(state.current)

                    if not state.queue:
                        self._clear_current_locked(state)
                        state.stopped = False
                        self._cancel_playback_watchdog_locked(state)
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
                state.last_progress_seconds = 0
                state.last_progress_checked_at = self._now()
                state.stuck_count = 0
                state.not_playing_since = None
                state.last_activity_at = self._now()
                volume = state.volume
                self._ensure_voice_monitor_locked(guild_id, state)
                self._ensure_playback_watchdog_locked(guild_id, state)

            if await self._start_current(guild_id, queued_track, volume):
                await self.on_track_start(guild_id)
                return True

            async with state.lock:
                state.history.appendleft(queued_track)
                state.consecutive_failures += 1
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
            state.last_activity_at = self._now()

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
            state.last_activity_at = self._now()

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
            state.last_activity_at = self._now()

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
            self._invalidate_preload_locked(state)
            self._cancel_playback_watchdog_locked(state)
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
            state.last_activity_at = self._now()

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
            state.last_activity_at = self._now()
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
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)

        await self._preload_next_track(guild_id)
        await self.on_state_change(guild_id)
        return removed

    async def move(
        self,
        guild_id: int,
        from_index: int,
        to_index: int,
    ) -> bool:
        state = self.get_state(guild_id)
        async with state.lock:
            queue_size = len(state.queue)
            if (
                from_index < 1
                or from_index > queue_size
                or to_index < 1
                or to_index > queue_size
            ):
                return False

            if from_index == to_index:
                state.last_activity_at = self._now()
                return True

            item = state.queue[from_index - 1]
            del state.queue[from_index - 1]
            state.queue.insert(to_index - 1, item)
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)

        await self._preload_next_track(guild_id)
        await self.on_state_change(guild_id)
        return True

    async def previous(self, guild_id: int) -> QueuedTrack | None:
        state = self.get_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        voice_client = self.get_voice_client(guild) if guild is not None else None
        if voice_client is None or not voice_client.is_connected():
            return None

        async with state.lock:
            if not state.history:
                return None

            previous_track = state.history.popleft()
            if state.current is not None:
                state.queue.appendleft(state.current)

            state.current = previous_track
            state.started_at = self._now()
            state.paused_at = None
            state.paused_duration = timedelta()
            state.current_retries = 0
            state.stuck_count = 0
            state.consecutive_failures = 0
            state.last_progress_seconds = 0
            state.last_progress_checked_at = self._now()
            state.not_playing_since = None
            state.last_error = None
            state.stopped = False
            state.last_activity_at = self._now()
            state.recovery_in_progress = True
            volume = state.volume
            self._invalidate_preload_locked(state)
            self._ensure_voice_monitor_locked(guild_id, state)
            self._ensure_playback_watchdog_locked(guild_id, state)

        if voice_client is not None and (
            voice_client.is_playing() or voice_client.is_paused()
        ):
            voice_client.stop()
            await asyncio.sleep(0.25)

        started = await self._start_current(guild_id, previous_track, volume, force_refresh=True)
        async with state.lock:
            state.recovery_in_progress = False

        if started:
            await self.on_track_start(guild_id)
            return previous_track

        await self.on_player_error(guild_id, "Khong the phat lai bai truoc do.")
        return None

    async def shuffle(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        async with state.lock:
            if len(state.queue) < 2:
                return False

            items = list(state.queue)
            random.shuffle(items)
            state.queue = deque(items)
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)

        await self._preload_next_track(guild_id)
        await self.on_state_change(guild_id)
        return True

    async def clear_queue(self, guild_id: int) -> int:
        state = self.get_state(guild_id)
        async with state.lock:
            removed_count = len(state.queue)
            state.queue.clear()
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)

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
        state = self.get_state(guild_id)
        async with state.lock:
            state.last_activity_at = self._now()
            state.consecutive_failures = 0
        await self._preload_next_track(guild_id)
        await self.on_state_change(guild_id)

    async def on_track_end(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.last_activity_at = self._now()
        await self.on_state_change(guild_id)

    async def on_queue_end(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)
            self._cancel_playback_watchdog_locked(state)
        await self.on_state_change(guild_id)

    async def on_player_error(self, guild_id: int, error: Exception | str) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.last_error = str(error)
            state.last_activity_at = self._now()
        await self.on_state_change(guild_id)

    async def on_player_stopped(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.last_activity_at = self._now()
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
            state.last_activity_at = self._now()
            self._invalidate_preload_locked(state)
            self._cancel_playback_watchdog_locked(state)

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
        *,
        force_refresh: bool = False,
    ) -> bool:
        for attempt in range(MAX_TRACK_RETRIES + 1):
            refreshed_track = None
            if attempt == 0 and not force_refresh:
                refreshed_track = await self._consume_preloaded_track(
                    guild_id,
                    queued_track,
                )

            if refreshed_track is None:
                refreshed_track = await self.youtube.refresh(queued_track.track)

            if refreshed_track is not None:
                self._apply_track_refresh(queued_track.track, refreshed_track)

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
                    state.consecutive_failures += 1
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

    async def _preload_next_track(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            if not state.queue:
                self._invalidate_preload_locked(state)
                return

            next_track = state.queue[0]
            next_key = self._track_key(next_track)
            if (
                state.preloaded_track_key == next_key
                and state.preloaded_track is not None
            ):
                return

            current_task = state.preload_task
            if current_task is not None and not current_task.done():
                current_task.cancel()

            state.preloaded_track_key = next_key
            state.preloaded_track = None
            state.preload_error = None
            state.preload_task = self.loop.create_task(
                self._run_preload(guild_id, next_track, next_key)
            )

    async def _run_preload(
        self,
        guild_id: int,
        queued_track: QueuedTrack,
        track_key: str,
    ) -> None:
        try:
            refreshed_track = await self.youtube.refresh(queued_track.track)
            state = self.get_state(guild_id)
            async with state.lock:
                if state.preloaded_track_key != track_key:
                    return
                if refreshed_track is None:
                    state.preload_error = "Khong preload duoc bai tiep theo."
                    return
                state.preloaded_track = refreshed_track
                state.preload_error = None
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Failed to preload next track")
            state = self.get_state(guild_id)
            async with state.lock:
                if state.preloaded_track_key == track_key:
                    state.preload_error = str(exc)

    async def _consume_preloaded_track(
        self,
        guild_id: int,
        queued_track: QueuedTrack,
    ) -> YouTubeTrack | None:
        state = self.get_state(guild_id)
        async with state.lock:
            track_key = self._track_key(queued_track)
            if state.preloaded_track_key != track_key or state.preloaded_track is None:
                return None

            preloaded_track = state.preloaded_track
            state.preload_task = None
            state.preloaded_track_key = None
            state.preloaded_track = None
            state.preload_error = None
            return preloaded_track

    def _apply_track_refresh(
        self,
        target: YouTubeTrack,
        refreshed: YouTubeTrack,
    ) -> None:
        target.stream_url = refreshed.stream_url
        target.thumbnail_url = refreshed.thumbnail_url
        target.uploader = refreshed.uploader
        target.source = refreshed.source

    def _track_key(self, queued_track: QueuedTrack) -> str:
        return queued_track.track.webpage_url or queued_track.track.title

    def _after_play(self, guild_id: int, error: Exception | None) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._handle_after_play(guild_id, error),
            self.loop,
        )
        future.add_done_callback(self._log_after_error)

    async def _handle_after_play(
        self,
        guild_id: int,
        error: Exception | None,
    ) -> None:
        state = self.get_state(guild_id)

        async with state.lock:
            if state.recovery_in_progress:
                return

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

        state.voice_monitor_task = self.loop.create_task(
            self._monitor_voice_connection(guild_id)
        )

    def _ensure_playback_watchdog_locked(
        self,
        guild_id: int,
        state: GuildMusicState,
    ) -> None:
        task = state.playback_watchdog_task
        if task is not None and not task.done():
            return

        state.playback_watchdog_task = self.loop.create_task(
            self._monitor_playback_progress(guild_id)
        )

    async def _monitor_playback_progress(self, guild_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(PLAYBACK_WATCHDOG_INTERVAL)

                state = self.get_state(guild_id)
                guild = self.bot.get_guild(guild_id)
                voice_client = self.get_voice_client(guild) if guild is not None else None
                should_recover = False

                async with state.lock:
                    if state.current is None:
                        state.playback_watchdog_task = None
                        return

                    if state.paused_at is not None or state.voice_status != "connected":
                        state.not_playing_since = None
                        state.last_progress_seconds = self.get_elapsed_seconds(guild_id)
                        state.last_progress_checked_at = self._now()
                        continue

                    is_playing = voice_client is not None and voice_client.is_playing()
                    if is_playing:
                        state.not_playing_since = None
                        state.last_progress_seconds = self.get_elapsed_seconds(guild_id)
                        state.last_progress_checked_at = self._now()
                        continue

                    if state.not_playing_since is None:
                        state.not_playing_since = self._now()
                        continue

                    stuck_for = (self._now() - state.not_playing_since).total_seconds()
                    if stuck_for >= PLAYBACK_STUCK_TIMEOUT:
                        state.stuck_count += 1
                        state.last_error = "May phat nhac dang bi ket, dang thu khoi phuc..."
                        state.last_activity_at = self._now()
                        should_recover = True

                if should_recover:
                    recovered = await self._recover_current_track(guild_id)
                    if not recovered:
                        await self._skip_after_stuck(guild_id)
                        return
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Playback watchdog failed for guild %s", guild_id)

    async def _recover_current_track(self, guild_id: int) -> bool:
        state = self.get_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        voice_client = self.get_voice_client(guild) if guild is not None else None

        async with state.lock:
            if state.current is None:
                return False
            if state.stuck_count > MAX_TRACK_RETRIES:
                state.consecutive_failures += 1
                return False

            queued_track = state.current
            volume = state.volume
            state.recovery_in_progress = True
            state.current_retries += 1

        if voice_client is not None and (
            voice_client.is_playing() or voice_client.is_paused()
        ):
            voice_client.stop()
            await asyncio.sleep(0.25)

        async with state.lock:
            state.started_at = self._now()
            state.paused_at = None
            state.paused_duration = timedelta()
            state.not_playing_since = None
            state.last_progress_seconds = 0
            state.last_progress_checked_at = self._now()

        recovered = await self._start_current(
            guild_id,
            queued_track,
            volume,
            force_refresh=True,
        )
        async with state.lock:
            state.recovery_in_progress = False

        if recovered:
            await self.on_state_change(guild_id)
            return True

        return False

    async def _skip_after_stuck(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        async with state.lock:
            state.last_error = "Bai hien tai bi ket qua lau. May phat nhac da bo qua bai nay."
            state.skip_requested = True
            state.recovery_in_progress = False

        await self.play_next(guild_id)

    def _invalidate_preload_locked(self, state: GuildMusicState) -> None:
        if state.preload_task is not None and not state.preload_task.done():
            state.preload_task.cancel()
        state.preload_task = None
        state.preloaded_track_key = None
        state.preloaded_track = None
        state.preload_error = None

    def _cancel_playback_watchdog_locked(self, state: GuildMusicState) -> None:
        if state.playback_watchdog_task is not None and not state.playback_watchdog_task.done():
            state.playback_watchdog_task.cancel()
        state.playback_watchdog_task = None
        state.not_playing_since = None

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

    async def _cleanup_idle_states(self) -> None:
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL)
                self.youtube.clear_expired_cache()

                for guild_id, state in list(self._states.items()):
                    guild = self.bot.get_guild(guild_id)
                    voice_client = self.get_voice_client(guild) if guild is not None else None
                    async with state.lock:
                        idle_for = (self._now() - state.last_activity_at).total_seconds()
                        is_voice_connected = (
                            voice_client is not None and voice_client.is_connected()
                        )
                        is_idle = (
                            state.current is None
                            and not state.queue
                            and (not is_voice_connected or state.stopped)
                            and idle_for >= IDLE_CLEANUP_TIMEOUT
                        )
                        if not is_idle:
                            continue

                        state.history.clear()
                        state.last_error = None
                        state.preload_error = None
                        self._invalidate_preload_locked(state)
                        self._cancel_playback_watchdog_locked(state)
                        if state.voice_monitor_task is not None and not state.voice_monitor_task.done():
                            state.voice_monitor_task.cancel()
                        state.voice_monitor_task = None
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Cleanup loop failed")

    def _clear_current_locked(self, state: GuildMusicState) -> None:
        state.current = None
        state.started_at = None
        state.paused_at = None
        state.paused_duration = timedelta()
        state.current_retries = 0
        state.not_playing_since = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


class QueueFullError(Exception):
    def __init__(self, max_size: int) -> None:
        super().__init__(f"Queue is full ({max_size} tracks)")
        self.max_size = max_size
