import discord


def has_manage_messages_permission(user: discord.Member) -> bool:
    return user.guild_permissions.manage_messages


def has_admin_permission(user: discord.Member) -> bool:
    return user.guild_permissions.administrator