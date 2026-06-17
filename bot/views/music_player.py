from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord

if TYPE_CHECKING:
    from bot.cogs.music import MusicCog
    from bot.services.music_player import LoopMode


class MusicPlayerView(discord.ui.View):
    def __init__(
        self,
        music_cog: MusicCog,
        guild_id: int,
        *,
        loop_mode: LoopMode,
        is_paused: bool,
        has_current: bool,
        queue_size: int,
    ):
        super().__init__(timeout=None)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self._style_controls(
            loop_mode=loop_mode,
            is_paused=is_paused,
            has_current=has_current,
            queue_size=queue_size,
        )

    def _style_controls(
        self,
        *,
        loop_mode: LoopMode,
        is_paused: bool,
        has_current: bool,
        queue_size: int,
    ) -> None:
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                if isinstance(item, discord.ui.Select) and item.custom_id == "music_loop":
                    item.placeholder = f"Loop: {loop_mode}"
                    for option in item.options:
                        option.default = option.value == loop_mode
                continue

            if item.custom_id == "music_pause":
                item.label = "Resume" if is_paused else "Pause"
                item.emoji = "▶" if is_paused else "⏸"
                item.style = discord.ButtonStyle.success if is_paused else discord.ButtonStyle.primary
                item.disabled = not has_current
            elif item.custom_id in {"music_skip", "music_stop"}:
                item.disabled = not has_current
            elif item.custom_id == "music_shuffle":
                item.disabled = queue_size < 2

    async def _can_control(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Music controls chi dung duoc trong server nay.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Khong xac dinh duoc thanh vien trong server.",
                ephemeral=True,
            )
            return False

        allowed = await self.music_cog.can_use_player_control(
            interaction,
            interaction.user,
        )
        if not allowed:
            await interaction.response.send_message(
                "Ban can o cung voice channel voi bot hoac co quyen quan ly server.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Pause",
        emoji="⏸",
        style=discord.ButtonStyle.primary,
        custom_id="music_pause",
        row=0,
    )
    async def pause_resume(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicPlayerView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.toggle_pause_from_interaction(interaction)

    @discord.ui.button(
        label="Skip",
        emoji="⏭",
        style=discord.ButtonStyle.secondary,
        custom_id="music_skip",
        row=0,
    )
    async def skip(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicPlayerView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.skip_from_interaction(interaction)

    @discord.ui.button(
        label="Stop",
        emoji="⏹",
        style=discord.ButtonStyle.danger,
        custom_id="music_stop",
        row=0,
    )
    async def stop(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicPlayerView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.stop_from_interaction(interaction)

    @discord.ui.button(
        label="Queue",
        emoji="📜",
        style=discord.ButtonStyle.secondary,
        custom_id="music_queue",
        row=0,
    )
    async def queue(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicPlayerView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.send_queue_from_interaction(
            interaction,
            ephemeral=True,
        )

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="music_refresh",
        row=1,
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicPlayerView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.refresh_player_from_interaction(interaction)

    @discord.ui.button(
        label="Shuffle",
        emoji="🔀",
        style=discord.ButtonStyle.secondary,
        custom_id="music_shuffle",
        row=1,
    )
    async def shuffle(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[MusicPlayerView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        await self.music_cog.shuffle_from_interaction(interaction)

    @discord.ui.select(
        placeholder="Loop mode",
        custom_id="music_loop",
        min_values=1,
        max_values=1,
        row=2,
        options=[
            discord.SelectOption(
                label="Off",
                value="off",
                description="Play through once.",
            ),
            discord.SelectOption(
                label="Repeat Track",
                value="track",
                description="Replay the current track.",
            ),
            discord.SelectOption(
                label="Repeat Queue",
                value="queue",
                description="Keep the full queue moving.",
            ),
        ],
    )
    async def loop(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select[MusicPlayerView],
    ) -> None:
        if not await self._can_control(interaction):
            return

        loop_mode = cast("LoopMode", select.values[0])
        await self.music_cog.set_loop_from_interaction(interaction, loop_mode)
