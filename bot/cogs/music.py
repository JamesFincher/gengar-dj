"""Gengar DJ — Music creation commands (/create and /dj-spinup).

Routes song creation requests through Hermes webhook, which triggers
Gengar to generate a Suno lofi track. The result is delivered back
via the internal API callback.
"""

import asyncio
import json
import logging
import uuid

import aiohttp
import discord
from discord import slash_command, option
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
    """Handles /create and /dj-spinup for Suno song generation."""

    def __init__(self, bot):
        self.bot = bot

    @slash_command(
        name="create",
        description="Generate a new lofi track via Suno AI and optionally play it",
    )
    @option("prompt", description="Describe the vibe/inspiration for this lofi track")
    @option(
        "style",
        description="Style preset or custom description",
        choices=["rainy_lofi", "jazzhop", "citypop", "chill_ambient", "custom"],
        default="rainy_lofi"
    )
    @option("title", description="Title for the new track (optional)", default="")
    @option("play", description="Whether to play in voice channel after creation", default=True)
    async def create_song(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        style: str = "rainy_lofi",
        title: str = "",
        play: bool = True,
    ):
        await ctx.defer()  # non-ephemeral so we can edit later

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
            "guild_id": ctx.guild_id,
            "channel_id": ctx.channel_id,
            "user_id": ctx.author.id,
            "play_in_vc": play,
        }

        logger.info(
            "User %s requested /create: '%s' (style: %s)",
            ctx.author, prompt, style,
        )

        # Send progress embed — we'll edit this when the callback arrives
        progress_embed = discord.Embed(
            title="🎵 Gengar DJ — Spin Up",
            description=(
                f"**{track_title}**\n\n"
                f"*{prompt[:200]}{'…' if len(prompt) > 200 else ''}*\n\n"
                f"🔄 Dispatching to Suno…\n"
                f"⬜ Composing track\n"
                f"⬜ Uploading to R2\n"
                f"⬜ Ready to play"
            ),
            color=0x9B59B6,
        )
        progress_embed.set_footer(text="Gengar's Shadow Gateway • v5.5")

        # Send the response and capture the message for later editing
        response_msg = await ctx.respond(embed=progress_embed)
        # Get the actual Discord message to extract its ID
        try:
            original = await ctx.interaction.original_response()
            webhook_payload["response_channel_id"] = ctx.channel_id
            webhook_payload["response_message_id"] = original.id
        except Exception:
            pass  # best-effort; callback will fall back to new message

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
                    body = await resp.text()
                    if resp.status in (200, 202):
                        logger.info("Hermes webhook accepted /create request (status %d)", resp.status)
                    else:
                        logger.error("Hermes webhook returned %d: %s", resp.status, body)
                        await ctx.respond(
                            "||Failed to queue song creation. Gengar's webhook returned an error.||",
                            ephemeral=True,
                        )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("Hermes webhook connection error: %s", e)
            await ctx.respond(
                "||Couldn't reach Gengar. Please try again later.||",
                ephemeral=True,
            )

    @slash_command(
        name="dj-spinup",
        description="Spin up a custom Suno track securely through Gengar's shadow gateway!",
    )
    @option("prompt", description="The theme, topic, or lyrics for the song (e.g. 'a song about potatoes')")
    @option(
        "style",
        description="Select a preset genre or choose custom",
        choices=["rainy_lofi", "jazzhop", "citypop", "chill_ambient", "custom"],
        default="rainy_lofi"
    )
    @option("title", description="Custom title for the track", default="")
    async def dj_spinup(
        self,
        ctx: discord.ApplicationContext,
        prompt: str,
        style: str = "rainy_lofi",
        title: str = "",
    ):
        """Securely dispatch a song generation request directly to Gengar."""
        # Delegates directly to create_song with autoplay enabled (play=True)
        await self.create_song(
            ctx=ctx,
            prompt=prompt,
            style=style,
            title=title,
            play=True
        )

    # ─── /playlist commands ──────────────────────────────────────

    @slash_command(
        name="playlist",
        description="Show the current song playlist",
    )
    async def show_playlist(self, ctx: discord.ApplicationContext):
        import json
        try:
            res = await asyncio.to_thread(
                self.bot.s3_client.get_object,
                Bucket=self.bot.config.r2_bucket_name,
                Key="playlist.json"
            )
            entries = json.loads(res["Body"].read().decode("utf-8"))
        except self.bot.s3_client.exceptions.NoSuchKey:
            entries = []
        except Exception as e:
            logger.error("Failed to load R2 playlist: %s", e)
            await ctx.respond(
                "||Failed to load playlist from Cloudflare R2.||", ephemeral=True
            )
            return

        if not entries:
            await ctx.respond(
                "||The playlist is empty. Use /dj-spinup to make some songs!||",
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
            embed.set_footer(text="Use /dj-spinup to add more!")

        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(MusicCog(bot))
