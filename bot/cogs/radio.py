"""Gengar DJ — Radio commands (/radio).

Manages the silence-activated lofi radio in voice channels.
"""

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from ..player import RadioState

logger = logging.getLogger("gengar_dj.cogs.radio")

# Genre options for autocomplete
GENRES = ["lofi", "jazzhop", "citypop", "chill", "ambient", "rain", "all"]


class RadioCog(commands.Cog):
    """Silence-activated lofi radio for voice channels."""

    def __init__(self, bot):
        self.bot = bot

    def _get_state(self, guild_id: int) -> RadioState:
        """Get or create radio state for a guild."""
        if guild_id not in self.bot.radio_states:
            self.bot.radio_states[guild_id] = RadioState(guild_id, self.bot)
        return self.bot.radio_states[guild_id]

    # ─── /radio play ─────────────────────────────────────────────

    @app_commands.command(name="play", description="Start the lofi radio in your voice channel")
    @app_commands.describe(
        genre="Filter playlist by genre (optional)",
        silence="Seconds of silence before music starts (default: 25)",
    )
    async def radio_play(
        self,
        interaction: discord.Interaction,
        genre: str | None = None,
        silence: int | None = None,
    ):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "||You need to be in a voice channel first!||", ephemeral=True
            )
            return

        state = self._get_state(interaction.guild_id)
        channel = interaction.user.voice.channel

        if silence is not None:
            state.bot.config.silence_threshold = max(5, min(300, silence))

        if genre and genre.lower() != "all":
            state.genre_filter = genre.lower()

        await interaction.response.defer(ephemeral=True)

        success = await state.start_radio(channel)
        if success:
            embed = discord.Embed(
                title="📻 Gengar DJ — Radio On",
                description=(
                    f"Now listening for silence in **{channel.name}**\n"
                    f"Threshold: `{state.bot.config.silence_threshold}s` of silence\n"
                    f"Genre: `{state.genre_filter or 'all'}`\n"
                    f"Songs loaded: `{len(state.queue)}`"
                ),
                color=0x9B59B6,
            )
            embed.set_footer(text="Talk and the music fades • Radio resumes when quiet")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                "||Failed to join voice channel. Check my permissions.||", ephemeral=True
            )

    @radio_play.autocomplete("genre")
    async def genre_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=g.capitalize(), value=g)
            for g in GENRES if current.lower() in g
        ]

    # ─── /radio stop ─────────────────────────────────────────────

    @app_commands.command(name="stop", description="Stop the radio and leave voice")
    async def radio_stop(self, interaction: discord.Interaction):
        state = self._get_state(interaction.guild_id)
        if not state.active:
            await interaction.response.send_message(
                "||The radio isn't running.||", ephemeral=True
            )
            return

        await state.stop_radio()
        embed = discord.Embed(
            title="⏹ Gengar DJ — Radio Off",
            color=0xE74C3C,
        )
        await interaction.response.send_message(embed=embed)

        # Clean up state
        if interaction.guild_id in self.bot.radio_states:
            del self.bot.radio_states[interaction.guild_id]

    # ─── /radio skip ─────────────────────────────────────────────

    @app_commands.command(name="skip", description="Skip to the next track")
    async def radio_skip(self, interaction: discord.Interaction):
        state = self._get_state(interaction.guild_id)
        if not state.active or not state.vc or not state.vc.is_connected():
            await interaction.response.send_message(
                "||Radio isn't playing anything right now.||", ephemeral=True
            )
            return

        if state.vc.is_playing():
            state.vc.stop()
            await interaction.response.send_message(
                f"⏭ Skipped **{state.current_song.get('title', 'Unknown')}**",
                ephemerable=False,
            )
        else:
            await interaction.response.send_message(
                "||Nothing is playing right now.||", ephemeral=True
            )

    # ─── /radio volume ───────────────────────────────────────────

    @app_commands.command(name="volume", description="Set radio volume (0-100)")
    @app_commands.describe(level="Volume level from 0 to 100")
    async def radio_volume(self, interaction: discord.Interaction, level: int):
        if level < 0 or level > 100:
            await interaction.response.send_message(
                "||Volume must be between 0 and 100.||", ephemeral=True
            )
            return

        state = self._get_state(interaction.guild_id)
        vol = level / 100.0
        state.set_volume(vol)

        embed = discord.Embed(
            title="🔊 Volume",
            description=f"Radio volume set to `{level}%`",
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed)

    # ─── /radio status ───────────────────────────────────────────

    @app_commands.command(name="status", description="Show current radio status")
    async def radio_status(self, interaction: discord.Interaction):
        state = self._get_state(interaction.guild_id)
        if not state.active:
            await interaction.response.send_message(
                "||The radio is currently off. Use /play to start it.||",
                ephemeral=True,
            )
            return

        vc_name = state.vc.channel.name if state.vc and state.vc.channel else "Unknown"
        now_playing = state.current_song.get("title", "Nothing") if state.current_song else "Nothing"

        embed = discord.Embed(
            title="📻 Gengar DJ Status",
            color=0x9B59B6,
        )
        embed.add_field(name="Voice Channel", value=vc_name, inline=True)
        embed.add_field(name="Status", value="▶ Playing" if state.playing else "⏸ Listening", inline=True)
        embed.add_field(name="Now Playing", value=now_playing, inline=False)
        embed.add_field(name="Silence Threshold", value=f"{self.bot.config.silence_threshold}s", inline=True)
        embed.add_field(name="Volume", value=f"{int(state.volume * 100)}%", inline=True)
        embed.add_field(name="Genre Filter", value=state.genre_filter or "All", inline=True)
        embed.add_field(name="Songs in Queue", value=str(len(state.queue)), inline=True)

        await interaction.response.send_message(embed=embed)

    # ─── /radio genre ────────────────────────────────────────────

    @app_commands.command(name="genre", description="Filter the radio playlist by genre/style")
    @app_commands.describe(genre="Genre to filter by (lofi, jazzhop, citypop, chill, ambient, rain, all)")
    async def radio_genre(self, interaction: discord.Interaction, genre: str):
        state = self._get_state(interaction.guild_id)
        if genre.lower() == "all":
            state.genre_filter = None
        else:
            state.genre_filter = genre.lower()

        state._load_queue()
        await interaction.response.send_message(
            f"🎵 Genre filter set to `{genre}`. {len(state.queue)} songs in rotation.",
        )

    @radio_genre.autocomplete("genre")
    async def genre_filter_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=g.capitalize(), value=g)
            for g in GENRES if current.lower() in g
        ]

    # ─── /radio silence ──────────────────────────────────────────

    @app_commands.command(
        name="silence",
        description="Set how many seconds of silence before the radio starts",
    )
    @app_commands.describe(
        seconds="Seconds of silence before music (5-300)"
    )
    async def radio_silence(self, interaction: discord.Interaction, seconds: int):
        clamped = max(5, min(300, seconds))
        self.bot.config.silence_threshold = clamped
        await interaction.response.send_message(
            f"⏱ Radio silence threshold set to `{clamped}s`. Music starts after {clamped} seconds of quiet.",
        )


async def setup(bot):
    await bot.add_cog(RadioCog(bot))
