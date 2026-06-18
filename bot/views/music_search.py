from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.services.youtube import YouTubeTrack
from bot.utils.time import format_duration

if TYPE_CHECKING:
    from bot.cogs.music import MusicCog


class MusicSearchView(discord.ui.View):
    def __init__(
        self,
        music_cog: MusicCog,
        guild_id: int,
        requester_id: int,
        tracks: list[YouTubeTrack],
    ):
        super().__init__(timeout=180)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.tracks = tracks
        self.message: discord.Message | None = None

        select = MusicSearchSelect(tracks)
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Chi nguoi mo /search moi co the chon bai.",
                ephemeral=True,
            )
            return False

        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Search menu chi dung duoc trong server nay.",
                ephemeral=True,
            )
            return False

        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                return


class MusicSearchSelect(discord.ui.Select[MusicSearchView]):
    def __init__(self, tracks: list[YouTubeTrack]):
        options = []
        for index, track in enumerate(tracks):
            title = track.title[:90] if track.title else "Unknown title"
            duration = format_duration(track.duration)
            uploader = track.uploader or track.source or "YouTube"
            options.append(
                discord.SelectOption(
                    label=title,
                    value=str(index),
                    description=f"{uploader[:40]} • {duration}"[:100],
                )
            )

        super().__init__(
            placeholder="Chon bai de them vao queue",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.send_message(
                "Search menu khong con hop le.",
                ephemeral=True,
            )
            return

        selected_index = int(self.values[0])
        track = view.tracks[selected_index]
        await interaction.response.defer(ephemeral=True)
        await view.music_cog.add_search_result_from_interaction(
            interaction,
            track,
        )

        for item in view.children:
            item.disabled = True

        embed = view.music_cog.build_search_selected_embed(track)
        await interaction.message.edit(embed=embed, view=view)
