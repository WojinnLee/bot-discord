from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot.cogs.music import MusicCog


class MusicQueueView(discord.ui.View):
    def __init__(self, music_cog: MusicCog, guild_id: int, page: int = 0):
        super().__init__(timeout=180)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.page = page
        self._style_controls()

    def _style_controls(self) -> None:
        total_pages = self.music_cog.get_queue_total_pages(self.guild_id)
        state = self.music_cog.player.get_state(self.guild_id)

        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue

            if item.custom_id == "queue_prev":
                item.disabled = total_pages <= 1
            elif item.custom_id == "queue_next":
                item.disabled = total_pages <= 1
            elif item.custom_id == "queue_clear":
                item.disabled = len(state.queue) == 0
            elif item.custom_id == "queue_shuffle":
                item.disabled = len(state.queue) < 2

    async def _can_control(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Queue controls chi dung duoc trong server nay.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Khong xac dinh duoc thanh vien trong server.",
                ephemeral=True,
            )
            return False

        if not await self.music_cog.can_use_player_control(interaction, interaction.user):
            await interaction.response.send_message(
                "Ban can o cung voice channel voi bot hoac co quyen quan ly server.",
                ephemeral=True,
            )
            return False

        return True

    async def _edit(self, interaction: discord.Interaction) -> None:
        embed = self.music_cog.build_queue_embed(self.guild_id, self.page)
        view = MusicQueueView(self.music_cog, self.guild_id, self.page)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(
        label="Prev",
        emoji="◀",
        style=discord.ButtonStyle.secondary,
        custom_id="queue_prev",
    )
    async def prev_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicQueueView],
    ) -> None:
        total_pages = self.music_cog.get_queue_total_pages(self.guild_id)
        self.page = (self.page - 1) % max(total_pages, 1)
        await self._edit(interaction)

    @discord.ui.button(
        label="Next",
        emoji="▶",
        style=discord.ButtonStyle.secondary,
        custom_id="queue_next",
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicQueueView],
    ) -> None:
        total_pages = self.music_cog.get_queue_total_pages(self.guild_id)
        self.page = (self.page + 1) % max(total_pages, 1)
        await self._edit(interaction)

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="queue_refresh",
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicQueueView],
    ) -> None:
        await self._edit(interaction)

    @discord.ui.button(
        label="Clear",
        emoji="🗑",
        style=discord.ButtonStyle.danger,
        custom_id="queue_clear",
    )
    async def clear(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicQueueView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.player.clear_queue(self.guild_id)
        self.page = 0
        await self._edit(interaction)

    @discord.ui.button(
        label="Shuffle",
        emoji="🔀",
        style=discord.ButtonStyle.secondary,
        custom_id="queue_shuffle",
    )
    async def shuffle(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicQueueView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.player.shuffle(self.guild_id)
        self.page = 0
        await self._edit(interaction)
