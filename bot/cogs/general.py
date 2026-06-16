import discord
from discord import app_commands
from discord.ext import commands


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping_prefix(self, ctx: commands.Context):
        await ctx.send("Pong!")

    @app_commands.command(name="hello", description="Bot chào bạn")
    async def hello(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Xin chào {interaction.user.mention}!"
        )

    @app_commands.command(name="say", description="Bot nói lại nội dung bạn nhập")
    @app_commands.describe(text="Nội dung muốn bot nói")
    async def say(self, interaction: discord.Interaction, text: str):
        await interaction.response.send_message(text)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))