"""Gengar DJ — Admin commands.

Configuration and management for server admins.
"""

import logging
import json
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("gengar_dj.cogs.admin")


class AdminCog(commands.Cog):
    """Admin-only commands for managing the bot."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="reload",
        description="Reload the song library from disk (admin only)",
    )
    @app_commands.default_permissions(administrator=True)
    async def admin_reload(self, interaction: discord.Interaction):
        """Reload the playlist from disk."""
        guild_id = interaction.guild_id
        state = self.bot.radio_states.get(guild_id)
        if state:
            state._load_queue()
            await interaction.response.send_message(
                f"🔄 Reloaded playlist — {len(state.queue)} songs in rotation."
            )
        else:
            await interaction.response.send_message(
                "||No active radio session. Start one with /play first.||",
                ephemeral=True,
            )

    @app_commands.command(
        name="info",
        description="Show bot technical info",
    )
    async def admin_info(self, interaction: discord.Interaction):
        """Display bot and server information."""
        embed = discord.Embed(
            title="Gengar DJ — System Info",
            color=0x9B59B6,
        )
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Songs Dir", value=f"`{self.bot.config.songs_dir}`", inline=False)
        embed.add_field(name="Silence Threshold", value=f"{self.bot.config.silence_threshold}s", inline=True)
        embed.add_field(name="API Port", value=str(self.bot.config.bot_api_port), inline=True)

        # Count songs
        songs_dir = Path(self.bot.config.songs_dir)
        song_count = 0
        if songs_dir.exists():
            song_count = sum(1 for f in songs_dir.iterdir() if f.suffix.lower() in (".mp3", ".ogg", ".wav", ".flac"))
        embed.add_field(name="Local Songs", value=str(song_count), inline=True)

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
