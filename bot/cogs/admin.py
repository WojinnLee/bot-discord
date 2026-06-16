import discord
from discord import app_commands
from discord.ext import commands


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="clear", description="Xoá tin nhắn")
    @app_commands.describe(amount="Số tin nhắn muốn xoá hoặc nhập all để xoá nhiều nhất có thể")
    async def clear(self, interaction: discord.Interaction, amount: str):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "Bạn không có quyền xoá tin nhắn.",
                ephemeral=True
            )
            return

        if not interaction.channel.permissions_for(interaction.guild.me).manage_messages:
            await interaction.response.send_message(
                "Bot không có quyền Manage Messages.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        amount = amount.lower().strip()

        if amount == "all":
            limit = 1000
        else:
            if not amount.isdigit():
                await interaction.followup.send(
                    "Vui lòng nhập số hoặc `all`."
                )
                return

            limit = int(amount)

            if limit <= 0:
                await interaction.followup.send(
                    "Số lượng tin nhắn phải lớn hơn 0."
                )
                return

        deleted = await interaction.channel.purge(limit=limit)

        await interaction.followup.send(
            f"Đã xoá {len(deleted)} tin nhắn."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))