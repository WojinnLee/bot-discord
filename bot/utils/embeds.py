import discord


def success_embed(title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(title, description, discord.Color.green())


def error_embed(title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(title, description, discord.Color.red())


def info_embed(title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(title, description, discord.Color.blurple())


def _base_embed(
    title: str,
    description: str | None,
    color: discord.Color,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    if description:
        embed.description = description
    return embed
