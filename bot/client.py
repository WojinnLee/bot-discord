from pathlib import Path

import discord
from discord.ext import commands


class DiscordBot(commands.Bot):
    def __init__(self, command_prefix: str):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix=command_prefix,
            intents=intents
        )

    async def setup_hook(self):
        await self.load_cogs()

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

    async def on_ready(self):
        print(f"Bot đã online: {self.user}")