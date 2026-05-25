"""Gengar DJ — Radio commands (/radio).

Manages the silence-activated lofi radio in voice channels.
"""

import logging
from pathlib import Path

import discord
from discord import slash_command, option
from discord.ext import commands

from ..player import RadioState

logger = logging.getLogger("gengar_dj.cogs.radio")

# Genre options
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

    # ─── /play ───────────────────────────────────────────────────

    @slash_command(name="play", description="Start the lofi radio in your voice channel")
    @option("genre", description="Filter playlist by genre (optional)", choices=GENRES, required=False)
    @option("silence", description="Seconds of silence before music starts (default: 25)", required=False, type=int)
    async def radio_play(
        self,
        ctx: discord.ApplicationContext,
        genre: str = "all",
        silence: int = 25,
    ):
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.respond(
                "||You need to be in a voice channel first!||", ephemeral=True
            )
            return

        state = self._get_state(ctx.guild_id)
        channel = ctx.author.voice.channel

        if silence is not None:
            state.bot.config.silence_threshold = max(5, min(300, silence))

        if genre and genre.lower() != "all":
            state.genre_filter = genre.lower()

        await ctx.defer(ephemeral=True)

        success = await state.start_radio(channel)
        if success:
            mode_desc = ""
            if not state.recording_available:
                mode_desc = (
                    f"\\n⚠️ Voice detection unavailable (DAVE E2EE). "
                    f"Music starts after **{state.bot.config.silence_threshold}s** timer, "
                    f"then plays continuously."
                )
            embed = discord.Embed(
                title="📻 Gengar DJ — Radio On",
                description=(
                    f"Now listening for silence in **{channel.name}**\\n"
                    f"Threshold: `{state.bot.config.silence_threshold}s` of silence\\n"
                    f"Genre: `{state.genre_filter or 'all'}`\\n"
                    f"Songs loaded from R2: `{len(state.queue)}`"
                    f"{mode_desc}"
                ),
                color=0x9B59B6,
            )
            embed.set_footer(text="Talk and the music ducks • Radio resumes when quiet")
            await ctx.respond(embed=embed)
        else:
            await ctx.respond(
                "||Failed to join voice channel. Check my permissions.||", ephemeral=True
            )

    # ─── /stop ───────────────────────────────────────────────────

    @slash_command(name="stop", description="Stop the radio and leave voice")
    async def radio_stop(self, ctx: discord.ApplicationContext):
        state = self._get_state(ctx.guild_id)
        if not state.active:
            await ctx.respond(
                "||The radio isn't running.||", ephemeral=True
            )
            return

        await state.stop_radio()
        embed = discord.Embed(
            title="⏹ Gengar DJ — Radio Off",
            color=0xE74C3C,
        )
        await ctx.respond(embed=embed)

        # Clean up state
        if ctx.guild_id in self.bot.radio_states:
            del self.bot.radio_states[ctx.guild_id]

    # ─── /skip ───────────────────────────────────────────────────

    @slash_command(name="skip", description="Skip to the next track")
    async def radio_skip(self, ctx: discord.ApplicationContext):
        state = self._get_state(ctx.guild_id)
        if not state.active or not state.vc or not state.vc.is_connected():
            await ctx.respond(
                "||Radio isn't playing anything right now.||", ephemeral=True
            )
            return

        if state.vc.is_playing():
            state.vc.stop()
            await ctx.respond(
                f"⏭ Skipped **{state.current_song.get('title', 'Unknown')}**"
            )
        else:
            await ctx.respond(
                "||Nothing is playing right now.||", ephemeral=True
            )

    # ─── /volume ─────────────────────────────────────────────────

    @slash_command(name="volume", description="Set radio volume (0-100)")
    @option("level", description="Volume level from 0 to 100", type=int, min_value=0, max_value=100)
    async def radio_volume(self, ctx: discord.ApplicationContext, level: int):
        state = self._get_state(ctx.guild_id)
        vol = level / 100.0
        state.set_volume(vol)

        embed = discord.Embed(
            title="🔊 Volume",
            description=f"Radio volume set to `{level}%`",
            color=0x2ECC71,
        )
        await ctx.respond(embed=embed)

    # ─── /status ─────────────────────────────────────────────────

    @slash_command(name="status", description="Show current radio status")
    async def radio_status(self, ctx: discord.ApplicationContext):
        state = self._get_state(ctx.guild_id)
        if not state.active:
            await ctx.respond(
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

        await ctx.respond(embed=embed)

    # ─── /genre ──────────────────────────────────────────────────

    @slash_command(name="genre", description="Filter the radio playlist by genre/style")
    @option("genre", description="Genre to filter by", choices=GENRES)
    async def radio_genre(self, ctx: discord.ApplicationContext, genre: str):
        state = self._get_state(ctx.guild_id)
        if genre.lower() == "all":
            state.genre_filter = None
        else:
            state.genre_filter = genre.lower()

        await ctx.defer()
        await state._load_queue()
        await ctx.respond(
            f"🎵 Genre filter set to `{genre}`. {len(state.queue)} songs in rotation.",
        )

    # ─── /ducking ─────────────────────────────────────────────────

    @slash_command(name="ducking", description="Configure voice-activated volume ducking (fades when people talk)")
    @option("enabled", description="Enable or disable ducking", choices=["on", "off"], required=False)
    @option("level", description="Volume while people talk (5-50%)", type=int, min_value=5, max_value=50, required=False)
    @option("fade", description="Seconds to fade back to full volume (1-15)", type=int, min_value=1, max_value=15, required=False)
    async def radio_ducking(
        self,
        ctx: discord.ApplicationContext,
        enabled: str = None,
        level: int = None,
        fade: int = None,
    ):
        state = self._get_state(ctx.guild_id)
        changed = []

        if enabled is not None:
            state.ducking_enabled = (enabled == "on")
            changed.append(f"Ducking: `{'ON' if state.ducking_enabled else 'OFF'}`")

        if level is not None:
            state.duck_volume = level / 100.0
            changed.append(f"Duck volume: `{level}%`")

        if fade is not None:
            state.fade_back_seconds = float(fade)
            changed.append(f"Fade-back: `{fade}s`")

        if not changed:
            # Show current settings
            changed = [
                f"Ducking: `{'ON' if state.ducking_enabled else 'OFF'}`",
                f"Duck volume: `{int(state.duck_volume * 100)}%`",
                f"Fade-back: `{int(state.fade_back_seconds)}s`",
            ]

        note = (
            "\n\n⚠️ Voice detection is currently unavailable due to Discord's "
            "DAVE E2EE protocol. Ducking settings are saved and will activate "
            "automatically when py-cord ships DAVE receive support."
        ) if not state.recording_available else ""

        embed = discord.Embed(
            title="🔇 Gengar DJ — Ducking",
            description="\n".join(f"• {c}" for c in changed) + note,
            color=0x9B59B6,
        )
        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(RadioCog(bot))
