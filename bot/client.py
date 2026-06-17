from pathlib import Path
import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class DiscordBot(commands.Bot):
    def __init__(self, command_prefix: str):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix=command_prefix,
            intents=intents
        )
        self._guild_commands_cleared = False

    async def setup_hook(self):
        await self.load_cogs()
        self.tree.on_error = self.on_app_command_error

        synced = await self.tree.sync()
        print(f"Đã sync {len(synced)} slash command.")

    async def load_cogs(self):
        cogs_path = Path("bot/cogs")

        for file in cogs_path.glob("*.py"):
            if file.name.startswith("__"):
                continue

            extension = f"bot.cogs.{file.stem}"
            await self.load_extension(extension)
            print(f"Đã load cog: {extension}")

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception("Slash command failed", exc_info=error)

        message = "Lenh bi loi khi xu ly. Hay xem console de biet chi tiet."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.DiscordException:
            logger.exception("Failed to send slash command error response")

    async def on_ready(self):
        if not self._guild_commands_cleared:
            for guild in self.guilds:
                try:
                    self.tree.clear_commands(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    print(f"Cleared guild slash commands for {guild.id}: {len(synced)} left.")
                except discord.DiscordException:
                    logger.exception("Failed to clear guild slash commands for guild %s", guild.id)

            self._guild_commands_cleared = True

        print(f"Bot đã online: {self.user}")
