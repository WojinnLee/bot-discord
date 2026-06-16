import asyncio
import logging
import shutil
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.youtube import YouTubeService, YouTubeTrack


MAX_QUEUE_SIZE = 50

FFMPEG_BEFORE_OPTIONS = (
    "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 "
    "-reconnect_on_network_error 1 -reconnect_delay_max 5"
)
FFMPEG_OPTIONS = "-vn"

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueuedTrack:
    track: YouTubeTrack
    requester: discord.Member


@dataclass(slots=True)
class GuildMusicState:
    queue: deque[QueuedTrack] = field(default_factory=deque)
    current: QueuedTrack | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    suppress_next_after: bool = False


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.youtube = YouTubeService()
        self._states: dict[int, GuildMusicState] = {}

    def _get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    async def _send_guild_only_error(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Lenh nay chi dung duoc trong server.",
            ephemeral=True,
        )

    def _get_voice_client(self, guild: discord.Guild) -> discord.VoiceClient | None:
        return cast(discord.VoiceClient | None, guild.voice_client)

    def _ffmpeg_is_available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    async def _ensure_voice(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> discord.VoiceClient | None:
        if interaction.guild is None:
            return None

        if member.voice is None or member.voice.channel is None:
            await interaction.followup.send(
                "Ban can vao voice channel truoc khi phat nhac.",
                ephemeral=True,
            )
            return None

        voice_channel = member.voice.channel
        voice_client = self._get_voice_client(interaction.guild)

        try:
            if voice_client is None:
                return await voice_channel.connect()

            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)

            return voice_client
        except discord.ClientException:
            await interaction.followup.send(
                "Bot khong the ket noi voice channel.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Bot khong co quyen vao voice channel nay.",
                ephemeral=True,
            )
        except discord.DiscordException:
            await interaction.followup.send(
                "Co loi khi ket noi voice channel.",
                ephemeral=True,
            )

        return None

    async def _create_audio_source(self, track: YouTubeTrack) -> discord.FFmpegOpusAudio:
        return await discord.FFmpegOpusAudio.from_probe(
            track.stream_url,
            method="fallback",
            before_options=FFMPEG_BEFORE_OPTIONS,
            options=FFMPEG_OPTIONS,
        )

    async def _play_next(self, guild_id: int) -> bool:
        state = self._get_state(guild_id)

        while True:
            async with state.lock:
                if state.suppress_next_after:
                    state.suppress_next_after = False
                    state.current = None
                    return False

                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    state.current = None
                    return False

                voice_client = self._get_voice_client(guild)
                if voice_client is None or not voice_client.is_connected():
                    state.current = None
                    return False

                if voice_client.is_playing() or voice_client.is_paused():
                    return False

                if not state.queue:
                    state.current = None
                    return False

                queued_track = state.queue.popleft()
                state.current = queued_track

            refreshed_track = await self.youtube.refresh(queued_track.track)
            if refreshed_track is not None:
                queued_track.track.stream_url = refreshed_track.stream_url

            try:
                source = await self._create_audio_source(queued_track.track)
            except Exception:
                logger.exception("Failed to create FFmpeg audio source")
                async with state.lock:
                    state.current = None
                continue

            def after_play(error: Exception | None) -> None:
                if error:
                    logger.warning("Audio player error: %s", error)

                future = asyncio.run_coroutine_threadsafe(
                    self._play_next(guild_id),
                    self.bot.loop,
                )
                future.add_done_callback(self._log_after_error)

            try:
                voice_client.play(source, after=after_play)
                return True
            except discord.ClientException:
                logger.exception("Failed to start audio playback")
                async with state.lock:
                    state.current = None
                continue

    def _log_after_error(self, future: Future[bool]) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Error while playing next track")

    @app_commands.command(name="play", description="Phat nhac tu YouTube link hoac tu khoa")
    @app_commands.describe(query="YouTube link hoac tu khoa can tim")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await interaction.response.defer()

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                "Khong xac dinh duoc thanh vien trong server.",
                ephemeral=True,
            )
            return

        if not self._ffmpeg_is_available():
            await interaction.followup.send(
                "Khong tim thay FFmpeg. Hay cai FFmpeg va them vao PATH.",
                ephemeral=True,
            )
            return

        state = self._get_state(interaction.guild.id)

        async with state.lock:
            if len(state.queue) >= MAX_QUEUE_SIZE:
                await interaction.followup.send(
                    f"Queue da day ({MAX_QUEUE_SIZE} bai). Hay doi bot phat bot hoac dung /skip.",
                    ephemeral=True,
                )
                return

        track = await self.youtube.search(query)
        if track is None:
            await interaction.followup.send(
                "Khong lay duoc audio tu link hoac tu khoa nay.",
                ephemeral=True,
            )
            return

        voice_client = await self._ensure_voice(interaction, interaction.user)
        if voice_client is None:
            return

        queued_track = QueuedTrack(track=track, requester=interaction.user)

        async with state.lock:
            if state.current is not None and len(state.queue) >= MAX_QUEUE_SIZE:
                await interaction.followup.send(
                    f"Queue da day ({MAX_QUEUE_SIZE} bai). Hay doi bot phat bot hoac dung /skip.",
                    ephemeral=True,
                )
                return

            was_idle = (
                state.current is None
                and not voice_client.is_playing()
                and not voice_client.is_paused()
            )
            state.queue.append(queued_track)
            state.suppress_next_after = False

        if was_idle:
            started = await self._play_next(interaction.guild.id)
            if started:
                await interaction.followup.send(
                    f"Dang phat: **{track.title}**\n{track.webpage_url}"
                )
            else:
                await interaction.followup.send(
                    "Khong the bat dau phat bai nay. Hay kiem tra FFmpeg hoac thu bai khac.",
                    ephemeral=True,
                )
        else:
            await interaction.followup.send(
                f"Da them vao queue: **{track.title}**\nVi tri: {len(state.queue)}"
            )

    @app_commands.command(name="skip", description="Bo qua bai hien tai")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        voice_client = self._get_voice_client(interaction.guild)
        if voice_client is None or not voice_client.is_connected():
            await interaction.response.send_message(
                "Bot chua o voice channel.",
                ephemeral=True,
            )
            return

        if not voice_client.is_playing() and not voice_client.is_paused():
            await interaction.response.send_message(
                "Khong co bai nao dang phat.",
                ephemeral=True,
            )
            return

        voice_client.stop()
        await interaction.response.send_message("Da skip bai hien tai.")

    @app_commands.command(name="pause", description="Tam dung bai hien tai")
    async def pause(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        voice_client = self._get_voice_client(interaction.guild)
        if voice_client is None or not voice_client.is_playing():
            await interaction.response.send_message(
                "Khong co bai nao dang phat de pause.",
                ephemeral=True,
            )
            return

        voice_client.pause()
        await interaction.response.send_message("Da pause.")

    @app_commands.command(name="resume", description="Tiep tuc phat nhac")
    async def resume(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        voice_client = self._get_voice_client(interaction.guild)
        if voice_client is None or not voice_client.is_paused():
            await interaction.response.send_message(
                "Khong co bai nao dang pause.",
                ephemeral=True,
            )
            return

        voice_client.resume()
        await interaction.response.send_message("Da resume.")

    @app_commands.command(name="stop", description="Dung nhac va xoa queue")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        state = self._get_state(interaction.guild.id)
        voice_client = self._get_voice_client(interaction.guild)

        async with state.lock:
            state.queue.clear()
            state.current = None
            state.suppress_next_after = True

        if voice_client is not None and (
            voice_client.is_playing() or voice_client.is_paused()
        ):
            voice_client.stop()

        await interaction.response.send_message("Da stop va xoa queue.")

    @app_commands.command(name="leave", description="Bot roi khoi voice channel")
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        state = self._get_state(interaction.guild.id)
        voice_client = self._get_voice_client(interaction.guild)

        if voice_client is None or not voice_client.is_connected():
            await interaction.response.send_message(
                "Bot chua o voice channel.",
                ephemeral=True,
            )
            return

        async with state.lock:
            state.queue.clear()
            state.current = None
            state.suppress_next_after = True

        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()

        await voice_client.disconnect()
        await interaction.response.send_message("Da roi khoi voice channel.")

    @app_commands.command(name="queue", description="Xem queue hien tai")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        state = self._get_state(interaction.guild.id)

        current = state.current
        upcoming = list(state.queue)

        if current is None and not upcoming:
            await interaction.response.send_message(
                "Queue dang trong.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        if current is not None:
            lines.append(
                f"Dang phat: **{current.track.title}** - yeu cau boi {current.requester.mention}"
            )

        if upcoming:
            lines.append("Sap phat:")
            for index, queued_track in enumerate(upcoming[:10], start=1):
                lines.append(
                    f"{index}. **{queued_track.track.title}** - {queued_track.requester.mention}"
                )

            if len(upcoming) > 10:
                lines.append(f"... va {len(upcoming) - 10} bai nua.")

        await interaction.response.send_message("\n".join(lines))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
