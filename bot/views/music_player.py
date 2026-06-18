from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.services.youtube import YouTubeTrack
from bot.utils.time import format_duration

if TYPE_CHECKING:
    from bot.cogs.music import MusicCog
    from bot.services.music_player import LoopMode


RELATED_EMPTY_VALUE = "empty"


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
        has_previous: bool,
        related_tracks: list[YouTubeTrack],
    ):
        super().__init__(timeout=None)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.related_tracks = related_tracks[:8]

        self.add_item(PlayerRelatedSelect(self.related_tracks, disabled=not has_current))
        self.add_item(
            PlayerFunctionSelect(
                loop_mode=loop_mode,
                queue_size=queue_size,
                disabled=not has_current and queue_size == 0,
            )
        )
        self.add_item(PlayerRefreshButton())
        self.add_item(PlayerPreviousButton(disabled=not has_previous))
        self.add_item(PlayerPauseButton(is_paused=is_paused, disabled=not has_current))
        self.add_item(PlayerNextButton(disabled=not has_current))
        self.add_item(PlayerStopButton(disabled=not has_current))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Music controls chỉ dùng được trong server này.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Không xác định được thành viên trong server.",
                ephemeral=True,
            )
            return False

        allowed = await self.music_cog.can_use_player_control(interaction, interaction.user)
        if not allowed:
            await interaction.response.send_message(
                "Bạn cần ở cùng voice channel với bot hoặc có quyền quản lý server.",
                ephemeral=True,
            )
            return False

        return True


class PlayerRelatedSelect(discord.ui.Select[MusicPlayerView]):
    def __init__(self, tracks: list[YouTubeTrack], *, disabled: bool):
        options: list[discord.SelectOption] = []
        for index, track in enumerate(tracks):
            options.append(
                discord.SelectOption(
                    label=_truncate(f"{index + 1}. {track.title or 'Unknown title'}", 100),
                    value=str(index),
                    description=_truncate(
                        f"{format_duration(track.duration)} - {track.uploader or track.source or 'YouTube'}",
                        100,
                    ),
                    emoji="🎵",
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="Chưa có bài gợi ý",
                    value=RELATED_EMPTY_VALUE,
                    description="Bấm Search Tracks để tìm bài mới.",
                    emoji="🖍️",
                )
            )
            disabled = True

        super().__init__(
            custom_id="music_player_related",
            placeholder="▶ | Chọn một bài hát để thêm vào hàng đợi",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.send_message(
                "Player UI không còn hợp lệ.",
                ephemeral=True,
            )
            return

        value = self.values[0]
        if value == RELATED_EMPTY_VALUE:
            await interaction.response.send_message(
                "Chưa có bài gợi ý để thêm.",
                ephemeral=True,
            )
            return

        try:
            track = view.related_tracks[int(value)]
        except (ValueError, IndexError):
            await interaction.response.send_message(
                "Lựa chọn bài hát không hợp lệ.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await view.music_cog.add_player_related_from_interaction(interaction, track)


class PlayerFunctionSelect(discord.ui.Select[MusicPlayerView]):
    def __init__(self, *, loop_mode: LoopMode, queue_size: int, disabled: bool):
        options = [
            discord.SelectOption(
                label="Search Tracks",
                value="search",
                description="Tìm bài hát bằng search.",
                emoji="🔎",
            ),
            discord.SelectOption(
                label=f"Loop: {loop_mode}",
                value="loop",
                description="Đổi chế độ lặp Off/Track/Queue.",
                emoji="🔁",
            ),
            discord.SelectOption(
                label="Queue",
                value="queue",
                description="Mở hàng đợi hiện tại.",
                emoji="📜",
            ),
        ]
        if queue_size >= 2:
            options.append(
                discord.SelectOption(
                    label="Shuffle",
                    value="shuffle",
                    description="Trộn hàng đợi.",
                    emoji="🔀",
                )
            )

        super().__init__(
            custom_id="music_player_functions",
            placeholder="▶ | Chọn một chức năng khác để điều khiển máy phất nhạc",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.send_message(
                "Player UI không còn hợp lệ.",
                ephemeral=True,
            )
            return

        await view.music_cog.handle_player_function_from_interaction(
            interaction,
            self.values[0],
        )


class PlayerRefreshButton(discord.ui.Button[MusicPlayerView]):
    def __init__(self):
        super().__init__(
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="music_refresh",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is not None:
            await view.music_cog.refresh_player_from_interaction(interaction)


class PlayerPreviousButton(discord.ui.Button[MusicPlayerView]):
    def __init__(self, *, disabled: bool):
        super().__init__(
            emoji="⏮️",
            style=discord.ButtonStyle.secondary,
            custom_id="music_previous",
            row=2,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is not None:
            await view.music_cog.previous_from_interaction(interaction)


class PlayerPauseButton(discord.ui.Button[MusicPlayerView]):
    def __init__(self, *, is_paused: bool, disabled: bool):
        super().__init__(
            emoji="▶️" if is_paused else "⏸️",
            style=discord.ButtonStyle.success if is_paused else discord.ButtonStyle.primary,
            custom_id="music_pause",
            row=2,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is not None:
            await view.music_cog.toggle_pause_from_interaction(interaction)


class PlayerNextButton(discord.ui.Button[MusicPlayerView]):
    def __init__(self, *, disabled: bool):
        super().__init__(
            emoji="⏭️",
            style=discord.ButtonStyle.secondary,
            custom_id="music_skip",
            row=2,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is not None:
            await view.music_cog.skip_from_interaction(interaction)


class PlayerStopButton(discord.ui.Button[MusicPlayerView]):
    def __init__(self, *, disabled: bool):
        super().__init__(
            emoji="⏹️",
            style=discord.ButtonStyle.danger,
            custom_id="music_stop",
            row=2,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is not None:
            await view.music_cog.stop_from_interaction(interaction)


def _truncate(value: str, max_length: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3].rstrip()}..."
