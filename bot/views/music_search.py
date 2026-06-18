from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.services.youtube import YouTubeTrack
from bot.utils.time import format_duration

if TYPE_CHECKING:
    from bot.cogs.music import MusicCog


SEARCH_TIMEOUT_SECONDS = 60
CANCEL_VALUE = "cancel"


class MusicSearchModal(discord.ui.Modal, title="Search nhạc"):
    query = discord.ui.TextInput(
        label="Từ khóa",
        placeholder="see you again",
        min_length=1,
        max_length=120,
    )

    def __init__(self, music_cog: MusicCog):
        super().__init__()
        self.music_cog = music_cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        await self.music_cog.send_music_search_from_interaction(
            interaction,
            str(self.query.value),
            ephemeral=False,
        )


class MusicSearchView(discord.ui.View):
    def __init__(
        self,
        music_cog: MusicCog,
        guild_id: int,
        requester_id: int,
        tracks: list[YouTubeTrack],
        *,
        allowed_voice_channel_id: int | None = None,
    ):
        super().__init__(timeout=SEARCH_TIMEOUT_SECONDS)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.allowed_voice_channel_id = allowed_voice_channel_id
        self.tracks = tracks[:10]
        self.message: discord.Message | None = None
        self._finished = False

        self.add_item(MusicSearchSelect(self.guild_id, self.requester_id, self.tracks))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Search menu chỉ dùng được trong server này.",
                ephemeral=True,
            )
            return False

        if interaction.user.id == self.requester_id:
            return True

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Không xác định được thành viên trong server.",
                ephemeral=True,
            )
            return False

        if self._member_can_choose(interaction, interaction.user):
            return True

        await interaction.response.send_message(
            "Bạn cần là người mở search hoặc ở cùng voice channel hợp lệ.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        if self._finished:
            return

        self.disable_all_items()
        await self._edit_message(content="Search nhạc đã hết hạn.")

    def disable_all_items(self) -> None:
        for item in self.children:
            item.disabled = True

    async def mark_cancelled(self, interaction: discord.Interaction) -> None:
        self._finished = True
        self.disable_all_items()
        await interaction.response.defer(ephemeral=True)
        await self._edit_message(content="Đã hủy lựa chọn bài hát.")

    async def mark_selected(
        self,
        interaction: discord.Interaction,
        track: YouTubeTrack,
    ) -> None:
        self._finished = True
        self.disable_all_items()
        embed = self.music_cog.build_search_selected_embed(track)
        try:
            await interaction.message.edit(content=None, embed=embed, view=self, attachments=[])
        except discord.HTTPException:
            await self._edit_message(content=None, embed=embed)

    async def _edit_message(
        self,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
    ) -> None:
        message = self.message
        if message is None:
            return

        try:
            await message.edit(content=content, embed=embed, view=self)
        except discord.HTTPException:
            return

    def _member_can_choose(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> bool:
        member_channel = member.voice.channel if member.voice else None
        if member_channel is None:
            return False

        voice_client = self.music_cog.player.get_voice_client(interaction.guild)
        if voice_client is not None and voice_client.channel is not None:
            return member_channel == voice_client.channel

        return member_channel.id == self.allowed_voice_channel_id


class MusicSearchSelect(discord.ui.Select[MusicSearchView]):
    def __init__(
        self,
        guild_id: int,
        requester_id: int,
        tracks: list[YouTubeTrack],
    ):
        options = [
            discord.SelectOption(
                label="Hủy lựa chọn",
                value=CANCEL_VALUE,
                description="Đóng menu search này.",
                emoji="❌",
            )
        ]

        for index, track in enumerate(tracks):
            title = _truncate(track.title or "Unknown title", 92)
            duration = format_duration(track.duration)
            source = track.uploader or track.source or "YouTube"
            options.append(
                discord.SelectOption(
                    label=_truncate(f"{index + 1}. {title}", 100),
                    value=str(index),
                    description=_truncate(f"{duration} - {source}", 100),
                    emoji="▶",
                )
            )

        super().__init__(
            custom_id=f"music_search:{guild_id}:{requester_id}",
            placeholder="▶ | Chọn một bài hát để phát",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.send_message(
                "Search menu không còn hợp lệ.",
                ephemeral=True,
            )
            return

        value = self.values[0]
        if value == CANCEL_VALUE:
            await view.mark_cancelled(interaction)
            return

        try:
            selected_index = int(value)
            track = view.tracks[selected_index]
        except (ValueError, IndexError):
            await interaction.response.send_message(
                "Lựa chọn không hợp lệ.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await view.music_cog.add_search_result_from_interaction(interaction, track)
        await view.mark_selected(interaction, track)


def _truncate(value: str, max_length: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3].rstrip()}..."
