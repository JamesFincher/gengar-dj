"""Gengar DJ — Music creation commands (/create).

Routes song creation requests through Hermes webhook, which triggers
Gengar to generate a Suno lofi track. The result is delivered back
via the internal API callback.
"""

import json
import logging
import uuid

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("gengar_dj.cogs.music")

# Reusable songwriting style presets
STYLE_PRESETS = {
    "rainy_lofi": (
        "Rainy Japanese Lofi × Slowed Chillhop × Ambient Electronica (65 BPM) — "
        "Warm analog pads, gentle koto plucks, soft shakuhachi flute, heavy vinyl crackle, "
        "dusty tape warble, deep sub-bass, rain ambience throughout. Intro with rain and static, "
        "slow beat drop at 0:35, atmospheric breakdown, gentle fade."
    ),
    "jazzhop": (
        "Jazzhop × Lo-fi Hip Hop × Vintage Vinyl (78 BPM) — "
        "Warm Rhodes piano chords, brushed snare, upright bass walking lines, "
        "soft trumpet phrases, vinyl crackle and tape hiss. Mellow and unhurried with "
        "a midnight jazz club mood."
    ),
    "citypop": (
        "Japanese City Pop × Retro Funk × Summer Vibes (105 BPM) — "
        "Warm analog synths, funky slap bass, smooth electric piano, soft female vocal chops, "
        "crisp drum machine, tape saturation. Bright nostalgic 80s Tokyo summer night energy."
    ),
    "chill_ambient": (
        "Ambient Electronica × Sleep Chillhop × Drone Music (50 BPM) — "
        "Deep evolving synth pads, field recordings of rain and distant thunder, "
        "sparse gentle piano notes, deep sub-bass rumble, no percussion. "
        "Meditative and spacious. Perfect for deep focus or sleep."
    ),
}


class MusicCog(commands.Cog):
    """Handles /create for Suno song generation."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="create",
        description="Generate a new lofi track via Suno AI and optionally play it",
    )
    @app_commands.describe(
        prompt="Describe the vibe/inspiration for this lofi track",
        style="Style preset or custom description (rainy_lofi, jazzhop, citypop, chill_ambient, or custom)",
        title="Title for the new track (optional)",
        play="Whether to play in voice channel after creation (default: true)",
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="🌧 Rainy Lofi", value="rainy_lofi"),
        app_commands.Choice(name="🎷 Jazzhop", value="jazzhop"),
        app_commands.Choice(name="🌃 City Pop", value="citypop"),
        app_commands.Choice(name="🧘 Chill Ambient", value="chill_ambient"),
        app_commands.Choice(name="✍️ Custom (prompt)", value="custom"),
    ])
    async def create_song(
        self,
        interaction: discord.Interaction,
        prompt: str,
        style: str = "rainy_lofi",
        title: str | None = None,
        play: bool = True,
    ):
        await interaction.response.defer(ephemeral=True)

        # Build the style description
        if style == "custom":
            style_desc = prompt
        else:
            preset_style = STYLE_PRESETS.get(style, STYLE_PRESETS["rainy_lofi"])
            style_desc = f"{preset_style}\nTheme/Prompt: {prompt}"

        track_title = title or f"{prompt[:40]}".strip().title()
        track_title = track_title[:100]

        # Build the callback payload
        callback_url = f"{self.bot.config.bot_callback_url}/api/callback/song"
        webhook_payload = {
            "prompt": prompt,
            "style_tags": style_desc,
            "title": track_title,
            "callback_url": callback_url,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "user_id": interaction.user.id,
            "play_in_vc": play,
        }

        logger.info(
            "User %s requested /create: '%s' (style: %s)",
            interaction.user, prompt, style,
        )

        # Send initial acknowledgment
        embed = discord.Embed(
            title="🎵 Generating…",
            description=(
                f"**{track_title}**\n\n"
                f"*{prompt[:200]}*\n\n"
                "⏳ Sending to Hermes for Suno generation…\n"
                "This usually takes 1-2 minutes."
            ),
            color=0x9B59B6,
        )
        await interaction.followup.send(embed=embed, ephemeral=False)

        # POST to the Hermes webhook
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Content-Type": "application/json"}
                if self.bot.config.hermes_webhook_secret:
                    import hmac, hashlib
                    sig = hmac.new(
                        self.bot.config.hermes_webhook_secret.encode(),
                        json.dumps(webhook_payload).encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    headers["X-Hub-Signature-256"] = f"sha256={sig}"

                async with session.post(
                    self.bot.config.hermes_webhook_url,
                    json=webhook_payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        logger.info("Hermes webhook accepted /create request")
                    else:
                        body = await resp.text()
                        logger.error(
                            "Hermes webhook returned %d: %s", resp.status, body
                        )
                        await interaction.followup.send(
                            "||Failed to queue song creation. Gengar's webhook returned an error.||",
                            ephemeral=True,
                        )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("Hermes webhook connection error: %s", e)
            await interaction.followup.send(
                "||Couldn't reach Hermes. Please try again later.||",
                ephemeral=True,
            )

    # ─── /playlist commands ──────────────────────────────────────

    @app_commands.command(
        name="playlist",
        description="Show the current song playlist",
    )
    async def show_playlist(self, interaction: discord.Interaction):
        from ..api import APIServer
        # Playlist is managed by the API server, but we can read it directly
        import json
        from pathlib import Path
        pl = Path(self.bot.config.playlist_file)
        if not pl.exists():
            await interaction.response.send_message(
                "||The playlist is empty. Use /create to make some songs!||",
                ephemeral=True,
            )
            return

        try:
            entries = json.loads(pl.read_text())
        except (json.JSONDecodeError, OSError):
            await interaction.response.send_message(
                "||The playlist file is corrupted.||", ephemeral=True
            )
            return

        if not entries:
            await interaction.response.send_message(
                "||The playlist is empty. Use /create to make some songs!||",
                ephemeral=True,
            )
            return

        total = len(entries)
        # Show first 15
        shown = entries[:15]
        lines = []
        for i, e in enumerate(shown, 1):
            src = "☀️" if e.get("source") == "suno" else "📁"
            lines.append(f"`{i}.` {src} **{e['title'][:60]}**")

        embed = discord.Embed(
            title=f"📋 Playlist ({total} songs)",
            description="\n".join(lines),
            color=0x9B59B6,
        )
        if total > 15:
            embed.set_footer(text=f"Showing 15 of {total} songs")
        else:
            embed.set_footer(text="Use /create to add more!")

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(MusicCog(bot))
