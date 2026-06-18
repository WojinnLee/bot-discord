import logging
import math
import shutil
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.music_player import (
    MAX_QUEUE_SIZE,
    LoopMode,
    MusicPlayerService,
    QueueFullError,
)
from bot.services.youtube import YouTubeService
from bot.utils.embeds import error_embed, info_embed, success_embed
from bot.utils.time import format_duration


QUEUE_PAGE_SIZE = 10
PROGRESS_BAR_WIDTH = 18
SEARCH_RESULT_LIMIT = 5
PLAYLIST_ADD_LIMIT = 25

logger = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.youtube = YouTubeService()
        self.player = MusicPlayerService(
            bot,
            self.youtube,
            self._refresh_or_create_player_message,
        )

    async def _send_guild_only_error(self, interaction: discord.Interaction) -> None:
        await self._send_interaction_embed(
            interaction,
            error_embed("Lenh nay chi dung duoc trong server."),
            ephemeral=True,
        )

    def _ffmpeg_is_available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def _create_player_view(self, guild_id: int) -> discord.ui.View:
        from bot.views.music_player import MusicPlayerView

        state = self.player.get_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        voice_client = self.player.get_voice_client(guild) if guild is not None else None
        is_paused = voice_client.is_paused() if voice_client is not None else False

        return MusicPlayerView(
            self,
            guild_id,
            loop_mode=state.loop_mode,
            is_paused=is_paused,
            has_current=state.current is not None,
            queue_size=len(state.queue),
        )

    def _create_queue_view(self, guild_id: int, page: int = 0) -> discord.ui.View:
        from bot.views.music_queue import MusicQueueView

        return MusicQueueView(self, guild_id, page)

    def _create_search_view(
        self,
        guild_id: int,
        requester_id: int,
        tracks: list,
    ) -> discord.ui.View:
        from bot.views.music_search import MusicSearchView

        return MusicSearchView(self, guild_id, requester_id, tracks)

    def _build_player_embed(self, guild: discord.Guild) -> discord.Embed:
        state = self.player.get_state(guild.id)
        current = state.current

        if current is None:
            title = "Máy phát nhạc đã dừng" if state.stopped else "Máy phát nhạc đang trống"
            description = "Không có bài nào đang phát."
            if state.queue:
                description = "Đang chuẩn bị phát bài tiếp theo."

            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.dark_gray(),
            )
            embed.add_field(name="Queue", value=f"{len(state.queue)} bai", inline=True)
            embed.add_field(name="Loop", value=state.loop_mode, inline=True)
            embed.add_field(name="Volume", value=f"{int(state.volume * 100)}%", inline=True)
            embed.add_field(
                name="Kết nối",
                value=self._format_voice_status(state.voice_status),
                inline=True,
            )
            if state.last_error:
                embed.add_field(name="Thông báo", value=state.last_error[:900], inline=False)
            embed.set_footer(text=f"{guild.name} • Máy phát nhạc")
            return embed

        voice_client = self.player.get_voice_client(guild)
        if state.voice_status == "reconnecting":
            status = "Đang kết nối lại"
            color = discord.Color.orange()
        elif state.voice_status == "disconnected":
            status = "Mất kết nối"
            color = discord.Color.red()
        elif voice_client is not None and voice_client.is_paused():
            status = "Paused"
            color = discord.Color.gold()
        else:
            status = "Playing"
            color = discord.Color.blurple()

        track = current.track
        artist = f"{track.uploader}\n" if track.uploader else ""
        embed = discord.Embed(
            title=f"Máy phát nhạc • {status}",
            description=f"{artist}**[{track.title}]({track.webpage_url})**",
            color=color,
        )
        embed.add_field(
            name="Progress",
            value=self._format_progress(guild.id, track.duration),
            inline=False,
        )
        embed.add_field(name="Requested by", value=current.requester.mention, inline=True)
        embed.add_field(name="Volume", value=f"{int(state.volume * 100)}%", inline=True)
        embed.add_field(name="Loop", value=state.loop_mode, inline=True)
        embed.add_field(name="Queue", value=f"{len(state.queue)} bai", inline=True)
        embed.add_field(name="Source", value=track.source or "YouTube", inline=True)
        embed.add_field(
            name="Kết nối",
            value=self._format_voice_status(state.voice_status),
            inline=True,
        )
        embed.add_field(
            name="Duration",
            value=format_duration(track.duration),
            inline=True,
        )
        if state.last_error:
            embed.add_field(name="Thông báo", value=state.last_error[:900], inline=False)
        if track.thumbnail_url:
            embed.set_image(url=track.thumbnail_url)
        embed.set_footer(
            text=f"{track.source or 'YouTube'} • Máy phát nhạc của {current.requester.display_name}"
        )
        return embed

    def _format_voice_status(self, voice_status: str) -> str:
        if voice_status == "reconnecting":
            return "Đang kết nối lại..."
        if voice_status == "disconnected":
            return "Đã mất kết nối"
        return "Đang kết nối"

    def _format_progress(self, guild_id: int, duration: int | None) -> str:
        elapsed = self.player.get_elapsed_seconds(guild_id)
        if duration is None or duration <= 0:
            return f"{format_duration(elapsed)} ━━━━━━━━━━━━━━━━━━ live"

        elapsed = min(elapsed, duration)
        ratio = elapsed / duration
        filled = min(PROGRESS_BAR_WIDTH - 1, max(0, int(ratio * PROGRESS_BAR_WIDTH)))
        bar = "━" * filled + "●" + "─" * (PROGRESS_BAR_WIDTH - filled - 1)
        return f"{format_duration(elapsed)} {bar} {format_duration(duration)}"

    def build_queue_embed(self, guild_id: int, page: int = 0) -> discord.Embed:
        guild = self.bot.get_guild(guild_id)
        state = self.player.get_state(guild_id)
        total_pages = self.get_queue_total_pages(guild_id)
        page = min(max(page, 0), total_pages - 1)
        start = page * QUEUE_PAGE_SIZE
        upcoming = list(state.queue)
        page_tracks = upcoming[start : start + QUEUE_PAGE_SIZE]

        title = f"Queue của {guild.name}" if guild is not None else "Queue máy phát nhạc"
        embed = discord.Embed(title=title, color=discord.Color.blurple())

        if state.current is not None:
            current = state.current
            embed.add_field(
                name="Dang phat",
                value=(
                    f"**[{current.track.title}]({current.track.webpage_url})**\n"
                    f"Yeu cau boi {current.requester.mention}"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="Dang phat", value="Khong co bai nao.", inline=False)

        if not page_tracks:
            embed.description = "Queue dang trong."
        else:
            lines = []
            for offset, queued_track in enumerate(page_tracks, start=start + 1):
                duration = format_duration(queued_track.track.duration)
                lines.append(
                    f"`{offset:02d}.` **[{queued_track.track.title}]({queued_track.track.webpage_url})** "
                    f"`{duration}` - {queued_track.requester.mention}"
                )
            embed.description = "\n".join(lines)

        embed.set_footer(
            text=f"Page {page + 1}/{total_pages} • {len(upcoming)} bai trong queue"
        )
        return embed

    def build_search_embed(self, query: str, tracks: list) -> discord.Embed:
        embed = discord.Embed(
            title="Ket qua tim kiem",
            description=f"Chon mot bai cho: **{query}**",
            color=discord.Color.blurple(),
        )
        for index, track in enumerate(tracks, start=1):
            uploader = track.uploader or track.source or "YouTube"
            embed.add_field(
                name=f"{index}. {track.title[:80]}",
                value=f"{uploader} • {format_duration(track.duration)}",
                inline=False,
            )
        return embed

    def build_search_selected_embed(self, track) -> discord.Embed:
        return success_embed(
            "Da chon bai hat.",
            f"**[{track.title}]({track.webpage_url})**",
        )

    def get_queue_total_pages(self, guild_id: int) -> int:
        state = self.player.get_state(guild_id)
        return max(1, math.ceil(len(state.queue) / QUEUE_PAGE_SIZE))

    async def _ensure_voice(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> discord.VoiceClient | None:
        if interaction.guild is None:
            return None

        if member.voice is None or member.voice.channel is None:
            await interaction.followup.send(
                embed=error_embed(
                    "Ban can vao voice channel truoc.",
                    "Hay vao mot voice channel roi dung lai lenh /play.",
                ),
                ephemeral=True,
            )
            return None

        voice_channel = member.voice.channel
        voice_client = self.player.get_voice_client(interaction.guild)

        try:
            if voice_client is None:
                return await voice_channel.connect()

            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)

            return voice_client
        except discord.ClientException:
            await interaction.followup.send(
                embed=error_embed("Bot khong the ket noi voice channel."),
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Bot khong co quyen vao voice channel nay."),
                ephemeral=True,
            )
        except discord.DiscordException:
            await interaction.followup.send(
                embed=error_embed("Co loi khi ket noi voice channel."),
                ephemeral=True,
            )

        return None

    async def _refresh_or_create_player_message(self, guild_id: int) -> None:
        state = self.player.get_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        embed = self._build_player_embed(guild)
        view = self._create_player_view(guild_id)

        if state.player_message_id is not None and state.player_channel_id is not None:
            channel = self.bot.get_channel(state.player_channel_id)
            if isinstance(channel, discord.abc.Messageable):
                try:
                    message = await channel.fetch_message(state.player_message_id)  # type: ignore[attr-defined]
                    await message.edit(embed=embed, view=view)
                    return
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    state.player_message_id = None
                    state.player_channel_id = None
                except Exception:
                    logger.exception("Failed to update player message")
                    return

        if state.last_text_channel_id is None:
            return

        channel = self.bot.get_channel(state.last_text_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return

        try:
            message = await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            logger.exception("Failed to create player message")
            return

        state.player_message_id = message.id
        state.player_channel_id = message.channel.id

    async def _send_player_message_from_interaction(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        state = self.player.get_state(interaction.guild.id)
        state.last_text_channel_id = interaction.channel_id
        embed = self._build_player_embed(interaction.guild)
        view = self._create_player_view(interaction.guild.id)

        if interaction.response.is_done():
            message = await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
            message = await interaction.original_response()

        state.player_message_id = message.id
        state.player_channel_id = message.channel.id

    async def can_use_player_control(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> bool:
        if interaction.guild is None:
            return False

        permissions = member.guild_permissions
        if permissions.manage_guild or permissions.administrator:
            return True

        voice_client = self.player.get_voice_client(interaction.guild)
        if voice_client is None or voice_client.channel is None:
            return member.voice is not None and member.voice.channel is not None

        return member.voice is not None and member.voice.channel == voice_client.channel

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if self.bot.user is None or member.id != self.bot.user.id:
            return

        await self.player.handle_bot_voice_state_update(
            member.guild.id,
            before,
            after,
        )

    async def _send_control_denied(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            await self._send_interaction_embed(
                interaction,
                error_embed("Khong xac dinh duoc thanh vien trong server."),
                ephemeral=True,
            )
            return True

        if not await self.can_use_player_control(interaction, interaction.user):
            await self._send_interaction_embed(
                interaction,
                error_embed(
                    "Ban khong the dieu khien máy phát nhạc.",
                    "Hay vao cung voice channel voi bot hoac can quyen quan ly server.",
                ),
                ephemeral=True,
            )
            return True

        return False

    async def _send_interaction_embed(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        *,
        ephemeral: bool = False,
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    async def skip_from_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        skipped = await self.player.skip(interaction.guild.id)
        if not skipped:
            await self._send_interaction_embed(
                interaction,
                error_embed("Khong co bai nao dang phat."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed("Da skip bai hien tai."),
            ephemeral=True,
        )

    async def toggle_pause_from_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        result = await self.player.toggle_pause(interaction.guild.id)
        messages = {
            "paused": success_embed("Da pause máy phát nhạc."),
            "resumed": success_embed("Da resume máy phát nhạc."),
            "missing": error_embed("Bot chua o voice channel."),
            "idle": error_embed("Khong co bai nao dang phat."),
        }
        await self._send_interaction_embed(
            interaction,
            messages[result],
            ephemeral=True,
        )

    async def stop_from_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await self.player.stop(interaction.guild.id)
        await self._send_interaction_embed(
            interaction,
            success_embed("Da stop va xoa queue."),
            ephemeral=True,
        )

    async def send_queue_from_interaction(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool = False,
    ) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        embed = self.build_queue_embed(interaction.guild.id)
        view = self._create_queue_view(interaction.guild.id)
        await self._send_queue_response(interaction, embed, view, ephemeral=ephemeral)

    async def _send_queue_response(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        view: discord.ui.View,
        *,
        ephemeral: bool,
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)

    async def refresh_player_from_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await self._refresh_or_create_player_message(interaction.guild.id)
        await self._send_interaction_embed(
            interaction,
            info_embed("Máy phát nhạc đã được cập nhật."),
            ephemeral=True,
        )

    async def shuffle_from_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        shuffled = await self.player.shuffle(interaction.guild.id)
        if not shuffled:
            await self._send_interaction_embed(
                interaction,
                error_embed("Can it nhat 2 bai trong queue de shuffle."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed("Da shuffle queue."),
            ephemeral=True,
        )

    async def set_loop_from_interaction(
        self,
        interaction: discord.Interaction,
        loop_mode: LoopMode,
        *,
        ephemeral: bool = True,
    ) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await self.player.set_loop(interaction.guild.id, loop_mode)
        await self._send_interaction_embed(
            interaction,
            success_embed("Da cap nhat loop.", f"Loop mode: **{loop_mode}**"),
            ephemeral=ephemeral,
        )

    async def add_search_result_from_interaction(
        self,
        interaction: discord.Interaction,
        track,
    ) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if not isinstance(interaction.user, discord.Member):
            await self._send_interaction_embed(
                interaction,
                error_embed("Khong xac dinh duoc thanh vien trong server."),
                ephemeral=True,
            )
            return

        if not self._ffmpeg_is_available():
            await self._send_interaction_embed(
                interaction,
                error_embed(
                    "Khong tim thay FFmpeg.",
                    "Hay cai FFmpeg va them vao PATH truoc khi chon bai.",
                ),
                ephemeral=True,
            )
            return

        voice_client = await self._ensure_voice(interaction, interaction.user)
        if voice_client is None:
            return

        try:
            queued_track, was_idle = await self.player.add_track(
                interaction.guild,
                voice_client,
                track,
                interaction.user,
                interaction.channel_id,
            )
        except QueueFullError:
            await self._send_interaction_embed(
                interaction,
                error_embed("Queue da day.", f"Gioi han hien tai la {MAX_QUEUE_SIZE} bai."),
                ephemeral=True,
            )
            return

        if was_idle:
            state = self.player.get_state(interaction.guild.id)
            if state.player_message_id is None:
                await self._send_player_message_from_interaction(interaction)
            else:
                await self._refresh_or_create_player_message(interaction.guild.id)
        else:
            await self._refresh_or_create_player_message(interaction.guild.id)

        await self._send_interaction_embed(
            interaction,
            success_embed(
                "Da them bai da chon.",
                f"**[{queued_track.track.title}]({queued_track.track.webpage_url})**",
            ),
            ephemeral=True,
        )

    async def _handle_playlist_play(
        self,
        interaction: discord.Interaction,
        query: str,
        *,
        play_next: bool,
    ) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False

        if not self.youtube.is_playlist_url(query):
            return False

        available_slots = self.player.available_queue_slots(interaction.guild.id)
        if available_slots <= 0:
            await interaction.followup.send(
                embed=error_embed(
                    "Queue da day.",
                    f"Gioi han hien tai la {MAX_QUEUE_SIZE} bai.",
                ),
                ephemeral=True,
            )
            return True

        limit = min(PLAYLIST_ADD_LIMIT, available_slots)
        tracks = await self.youtube.extract_playlist(
            query,
            limit=PLAYLIST_ADD_LIMIT,
            available_slots=available_slots,
        )
        if not tracks:
            await interaction.followup.send(
                embed=error_embed(
                    "Playlist khong co bai hop le.",
                    "Playlist co the rong, private, hoac co bai khong the phat.",
                ),
                ephemeral=True,
            )
            return True

        voice_client = await self._ensure_voice(interaction, interaction.user)
        if voice_client is None:
            return True

        try:
            if play_next:
                added_count, was_idle = await self.player.insert_tracks_next(
                    interaction.guild,
                    voice_client,
                    tracks,
                    interaction.user,
                    interaction.channel_id,
                )
            else:
                added_count, was_idle = await self.player.add_tracks(
                    interaction.guild,
                    voice_client,
                    tracks,
                    interaction.user,
                    interaction.channel_id,
                )
        except QueueFullError:
            await interaction.followup.send(
                embed=error_embed(
                    "Queue da day.",
                    f"Gioi han hien tai la {MAX_QUEUE_SIZE} bai.",
                ),
                ephemeral=True,
            )
            return True

        if added_count <= 0:
            await interaction.followup.send(
                embed=error_embed("Khong them duoc bai nao tu playlist."),
                ephemeral=True,
            )
            return True

        state = self.player.get_state(interaction.guild.id)
        if was_idle and state.player_message_id is None:
            await self._send_player_message_from_interaction(interaction)
        else:
            await self._refresh_or_create_player_message(interaction.guild.id)

        mode_text = "vao dau queue" if play_next and not was_idle else "vao queue"
        description = f"Da them **{added_count}** bai {mode_text}."
        if added_count == limit and available_slots < PLAYLIST_ADD_LIMIT:
            description += f"\nQueue chi con **{available_slots}** slot."
        elif added_count == PLAYLIST_ADD_LIMIT:
            description += f"\nDa dat gioi han **{PLAYLIST_ADD_LIMIT}** bai moi lan them playlist."

        await interaction.followup.send(
            embed=success_embed("Da them playlist.", description),
            ephemeral=True,
        )
        return True

    @app_commands.command(name="play", description="Phat nhac tu YouTube link hoac tu khoa")
    @app_commands.describe(query="YouTube link hoac tu khoa can tim")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await interaction.response.defer()

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                embed=error_embed("Khong xac dinh duoc thanh vien trong server."),
                ephemeral=True,
            )
            return

        if not self._ffmpeg_is_available():
            await interaction.followup.send(
                embed=error_embed(
                    "Khong tim thay FFmpeg.",
                    "Hay cai FFmpeg va them vao PATH truoc khi dung /play.",
                ),
                ephemeral=True,
            )
            return

        if await self._handle_playlist_play(interaction, query, play_next=False):
            return

        state = self.player.get_state(interaction.guild.id)
        async with state.lock:
            if len(state.queue) >= MAX_QUEUE_SIZE:
                await interaction.followup.send(
                    embed=error_embed(
                        "Queue da day.",
                        f"Gioi han hien tai la {MAX_QUEUE_SIZE} bai.",
                    ),
                    ephemeral=True,
                )
                return

        track = await self.youtube.search(query)
        if track is None:
            await interaction.followup.send(
                embed=error_embed(
                    "Khong tim thay bai hat.",
                    "Thu tu khoa khac hoac gui link YouTube truc tiep.",
                ),
                ephemeral=True,
            )
            return

        voice_client = await self._ensure_voice(interaction, interaction.user)
        if voice_client is None:
            return

        try:
            queued_track, was_idle = await self.player.add_track(
                interaction.guild,
                voice_client,
                track,
                interaction.user,
                interaction.channel_id,
            )
        except QueueFullError as exc:
            await interaction.followup.send(
                embed=error_embed(
                    "Queue da day.",
                    f"Gioi han hien tai la {exc.max_size} bai.",
                ),
                ephemeral=True,
            )
            return

        if was_idle:
            state = self.player.get_state(interaction.guild.id)
            if state.current is None:
                await interaction.followup.send(
                    embed=error_embed(
                        "Khong the bat dau phat bai nay.",
                        "Hay kiem tra FFmpeg, mang, hoac thu mot bai khac.",
                    ),
                    ephemeral=True,
                )
                return

            if state.player_message_id is None:
                await self._send_player_message_from_interaction(interaction)
            else:
                await interaction.followup.send(
                    embed=success_embed(
                        "Máy phát nhạc đã bắt đầu.",
                        f"**[{queued_track.track.title}]({queued_track.track.webpage_url})**",
                    ),
                    ephemeral=True,
                )
            return

        state = self.player.get_state(interaction.guild.id)
        await interaction.followup.send(
            embed=success_embed(
                "Da them vao queue.",
                f"**[{queued_track.track.title}]({queued_track.track.webpage_url})**\n"
                f"Vi tri: **{len(state.queue)}**",
            )
        )
        await self._refresh_or_create_player_message(interaction.guild.id)

    @app_commands.command(name="playnext", description="Them bai vao dau queue de phat tiep theo")
    @app_commands.describe(query="YouTube link hoac tu khoa can phat tiep theo")
    async def playnext(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await interaction.response.defer()

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                embed=error_embed("Khong xac dinh duoc thanh vien trong server."),
                ephemeral=True,
            )
            return

        if await self._send_control_denied(interaction):
            return

        if not self._ffmpeg_is_available():
            await interaction.followup.send(
                embed=error_embed(
                    "Khong tim thay FFmpeg.",
                    "Hay cai FFmpeg va them vao PATH truoc khi dung /playnext.",
                ),
                ephemeral=True,
            )
            return

        if await self._handle_playlist_play(interaction, query, play_next=True):
            return

        track = await self.youtube.search(query)
        if track is None:
            await interaction.followup.send(
                embed=error_embed(
                    "Khong tim thay bai hat.",
                    "Thu tu khoa khac hoac gui link YouTube truc tiep.",
                ),
                ephemeral=True,
            )
            return

        voice_client = await self._ensure_voice(interaction, interaction.user)
        if voice_client is None:
            return

        try:
            queued_track, was_idle = await self.player.insert_next(
                interaction.guild,
                voice_client,
                track,
                interaction.user,
                interaction.channel_id,
            )
        except QueueFullError as exc:
            await interaction.followup.send(
                embed=error_embed(
                    "Queue da day.",
                    f"Gioi han hien tai la {exc.max_size} bai.",
                ),
                ephemeral=True,
            )
            return

        if was_idle:
            state = self.player.get_state(interaction.guild.id)
            if state.current is None:
                await interaction.followup.send(
                    embed=error_embed(
                        "Khong the bat dau phat bai nay.",
                        "Hay kiem tra FFmpeg, mang, hoac thu mot bai khac.",
                    ),
                    ephemeral=True,
                )
                return

            if state.player_message_id is None:
                await self._send_player_message_from_interaction(interaction)
            else:
                await self._refresh_or_create_player_message(interaction.guild.id)

            await interaction.followup.send(
                embed=success_embed(
                    "May phat nhac da bat dau.",
                    f"**[{queued_track.track.title}]({queued_track.track.webpage_url})**",
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=success_embed(
                "Da them vao dau queue.",
                f"**[{queued_track.track.title}]({queued_track.track.webpage_url})**\n"
                "Bai nay se phat tiep theo.",
            ),
            ephemeral=True,
        )
        await self._refresh_or_create_player_message(interaction.guild.id)

    @app_commands.command(name="search", description="Tim nhac YouTube va chon bang menu")
    @app_commands.describe(query="Tu khoa can tim")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await interaction.response.defer(ephemeral=True)

        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send(
                embed=error_embed("Khong xac dinh duoc thanh vien trong server."),
                ephemeral=True,
            )
            return

        query = query.strip()
        if not query:
            await interaction.followup.send(
                embed=error_embed("Hay nhap tu khoa can tim."),
                ephemeral=True,
            )
            return

        tracks = await self.youtube.search_many(query, limit=SEARCH_RESULT_LIMIT)
        if not tracks:
            await interaction.followup.send(
                embed=error_embed(
                    "Khong tim thay ket qua.",
                    "Thu tu khoa khac hoac dung link YouTube truc tiep.",
                ),
                ephemeral=True,
            )
            return

        embed = self.build_search_embed(query, tracks)
        view = self._create_search_view(
            interaction.guild.id,
            interaction.user.id,
            tracks,
        )
        message = await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True,
            wait=True,
        )
        if hasattr(view, "message"):
            view.message = message

    @app_commands.command(name="skip", description="Bo qua bai hien tai")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        await self.skip_from_interaction(interaction)

    @app_commands.command(name="pause", description="Tam dung bai hien tai")
    async def pause(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        paused = await self.player.pause(interaction.guild.id)
        if not paused:
            await self._send_interaction_embed(
                interaction,
                error_embed("Khong co bai nao dang phat de pause."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed("Da pause máy phát nhạc."),
            ephemeral=True,
        )

    @app_commands.command(name="resume", description="Tiep tuc phat nhac")
    async def resume(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        resumed = await self.player.resume(interaction.guild.id)
        if not resumed:
            await self._send_interaction_embed(
                interaction,
                error_embed("Khong co bai nao dang pause."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed("Da resume máy phát nhạc."),
            ephemeral=True,
        )

    @app_commands.command(name="previous", description="Phat lai bai truoc do")
    async def previous(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await interaction.response.defer(ephemeral=True)

        if await self._send_control_denied(interaction):
            return

        previous_track = await self.player.previous(interaction.guild.id)
        if previous_track is None:
            await self._send_interaction_embed(
                interaction,
                error_embed("Khong co bai truoc do de phat lai."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed(
                "Dang phat lai bai truoc do.",
                f"**[{previous_track.track.title}]({previous_track.track.webpage_url})**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="stop", description="Dung nhac va xoa queue")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        await self.stop_from_interaction(interaction)

    @app_commands.command(name="leave", description="Bot roi khoi voice channel")
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        left = await self.player.leave(interaction.guild.id)
        if not left:
            await self._send_interaction_embed(
                interaction,
                error_embed("Bot chua o voice channel."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed("Da roi khoi voice channel."),
            ephemeral=True,
        )

    @app_commands.command(name="queue", description="Xem queue hien tai")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        await self.send_queue_from_interaction(interaction)

    @app_commands.command(name="nowplaying", description="Xem máy phát nhạc hien tai")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        state = self.player.get_state(interaction.guild.id)
        if state.player_message_id is None:
            await self._send_player_message_from_interaction(interaction)
            return

        state.last_text_channel_id = interaction.channel_id
        await self._refresh_or_create_player_message(interaction.guild.id)
        await self._send_interaction_embed(
            interaction,
            info_embed("Máy phát nhạc đã được cập nhật."),
            ephemeral=True,
        )

    @app_commands.command(name="volume", description="Chinh am luong theo server")
    @app_commands.describe(level="Am luong tu 0 den 100")
    async def volume(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 100],
    ) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        await self.player.set_volume(interaction.guild.id, int(level) / 100)
        await self._send_interaction_embed(
            interaction,
            success_embed("Da cap nhat volume.", f"Volume: **{int(level)}%**"),
            ephemeral=True,
        )

    @app_commands.command(name="loop", description="Chinh che do lap nhac")
    @app_commands.describe(mode="off, track hoac queue")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="track", value="track"),
            app_commands.Choice(name="queue", value="queue"),
        ]
    )
    async def loop(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        await self.set_loop_from_interaction(
            interaction,
            cast(LoopMode, mode.value),
            ephemeral=True,
        )

    @app_commands.command(name="remove", description="Xoa mot bai khoi queue")
    @app_commands.describe(index="Vi tri bai trong queue, bat dau tu 1")
    async def remove(self, interaction: discord.Interaction, index: int) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        removed = await self.player.remove(interaction.guild.id, index)
        if removed is None:
            await self._send_interaction_embed(
                interaction,
                error_embed("Vi tri queue khong hop le."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed("Da xoa khoi queue.", f"**{removed.track.title}**"),
            ephemeral=True,
        )

    @app_commands.command(name="move", description="Chuyen vi tri bai trong queue")
    @app_commands.describe(
        from_index="Vi tri bai can chuyen, bat dau tu 1",
        to_index="Vi tri moi, bat dau tu 1",
    )
    async def move(
        self,
        interaction: discord.Interaction,
        from_index: int,
        to_index: int,
    ) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        moved = await self.player.move(interaction.guild.id, from_index, to_index)
        if not moved:
            await self._send_interaction_embed(
                interaction,
                error_embed("Vi tri queue khong hop le."),
                ephemeral=True,
            )
            return

        await self._send_interaction_embed(
            interaction,
            success_embed(
                "Da chuyen vi tri bai trong queue.",
                f"Tu **{from_index}** sang **{to_index}**.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="shuffle", description="Tron queue hien tai")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        await self.shuffle_from_interaction(interaction)

    @app_commands.command(name="clearqueue", description="Xoa queue nhung khong dung bai dang phat")
    async def clearqueue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_guild_only_error(interaction)
            return

        if await self._send_control_denied(interaction):
            return

        removed_count = await self.player.clear_queue(interaction.guild.id)
        await self._send_interaction_embed(
            interaction,
            success_embed(
                "Da xoa queue.",
                f"Da xoa **{removed_count}** bai. Bai hien tai van tiep tuc phat.",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
