"""Gengar DJ — Admin commands.

Configuration and management for server admins.
"""

import logging
import json
from pathlib import Path

import discord
from discord import slash_command
from discord.ext import commands

logger = logging.getLogger("gengar_dj.cogs.admin")


class AdminCog(commands.Cog):
    """Admin-only commands for managing the bot."""

    def __init__(self, bot):
        self.bot = bot

    @slash_command(
        name="reload",
        description="Reload the song library from Cloudflare R2 (admin only)",
    )
    @commands.has_permissions(administrator=True)
    async def admin_reload(self, ctx: discord.ApplicationContext):
        """Reload the playlist from R2."""
        guild_id = ctx.guild_id
        state = self.bot.radio_states.get(guild_id)
        if state:
            await ctx.defer()
            await state._load_queue()
            await ctx.respond(
                f"🔄 Reloaded playlist — {len(state.queue)} songs in rotation."
            )
        else:
            await ctx.respond(
                "||No active radio session. Start one with /play first.||",
                ephemeral=True,
            )

    @slash_command(
        name="info",
        description="Show bot technical info",
    )
    async def admin_info(self, ctx: discord.ApplicationContext):
        """Display bot and server information."""
        embed = discord.Embed(
            title="Gengar DJ — System Info",
            color=0x9B59B6,
        )
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="R2 Bucket", value=f"`{self.bot.config.r2_bucket_name}`", inline=False)
        embed.add_field(name="Silence Threshold", value=f"{self.bot.config.silence_threshold}s", inline=True)
        embed.add_field(name="API Port", value=str(self.bot.config.bot_api_port), inline=True)

        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(AdminCog(bot))
