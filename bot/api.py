"""Internal HTTP API server for Gengar DJ.

Handles song creation callbacks from Hermes/Gengar and playlist management.
All metadata is preserved directly on Cloudflare R2.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger("gengar_dj.api")


class APIServer:
    """aiohttp server that receives song creation callbacks from Hermes."""

    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()
        self.s3_client = bot.s3_client

    def _setup_routes(self):
        self._app.router.add_post("/api/callback/song", self.handle_song_callback)
        self._app.router.add_get("/api/health", self.handle_health)
        self._app.router.add_get("/api/playlist", self.handle_get_playlist)
        self._app.router.add_post("/api/playlist/add", self.handle_add_to_playlist)
        self._app.router.add_post("/api/playlist/remove", self.handle_remove_from_playlist)

    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            self.config.bot_api_host,
            self.config.bot_api_port,
        )
        await site.start()
        logger.info(
            "API server listening on %s:%s",
            self.config.bot_api_host,
            self.config.bot_api_port,
        )

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    # ─── routes ────────────────────────────────────────────────

    async def handle_health(self, request):
        """Simple health check."""
        return web.json_response({"status": "ok", "guilds": len(self.bot.guilds)})

    async def handle_song_callback(self, request):
        """Receive completed song from Gengar after /create.

        Expected JSON payload:
        {
            "guild_id": int,
            "channel_id": int,
            "user_id": int,
            "title": str,
            "file_key": str,         // R2 object key of the uploaded song
            "play_in_vc": bool,
            "style_tags": str (optional),
            "suno_url": str (optional),        // Suno clip page URL
            "suno_clip_id": str (optional),    // Suno clip ID
            "response_channel_id": int (optional),  // to edit the progress embed
            "response_message_id": int (optional),
        }
        """
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        title = data.get("title", "Untitled")
        file_key = data.get("file_key")
        play_in_vc = data.get("play_in_vc", False)
        style_tags = data.get("style_tags", "")
        suno_url = data.get("suno_url", "")
        suno_clip_id = data.get("suno_clip_id", "")
        response_channel_id = data.get("response_channel_id")
        response_message_id = data.get("response_message_id")

        if not guild_id or not channel_id or not file_key:
            return web.json_response(
                {"error": "guild_id, channel_id, and file_key required"},
                status=400
            )

        logger.info("Received callback for fresh Suno track: %s (key: %s)", title, file_key)

        # Add to R2 playlist.json
        entry = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "file": file_key,
            "style_tags": style_tags,
            "source": "suno",
            "suno_url": suno_url,
            "suno_clip_id": suno_clip_id,
            "created_at": asyncio.get_event_loop().time(),
        }
        await asyncio.to_thread(self._append_to_r2_playlist_sync, entry)

        # Update the original progress embed if we have the message reference
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        
        if channel and response_message_id:
            try:
                msg = await channel.fetch_message(response_message_id)
            except Exception:
                msg = None
            
            if msg:
                # Edit the progress embed to show completion
                suno_link = f"\n🔗 [Open on Suno]({suno_url})" if suno_url else ""
                ready_embed = discord.Embed(
                    title="🎵 Gengar DJ — Track Ready!",
                    description=(
                        f"**{title}**\n\n"
                        f"✅ Dispatched to Suno\n"
                        f"✅ Composed & rendered\n"
                        f"✅ Uploaded to R2\n"
                        f"✅ Ready to play{suno_link}"
                    ),
                    color=0x2ECC71,
                )
                if suno_clip_id:
                    ready_embed.set_footer(text=f"Suno Clip: {suno_clip_id}")
                await msg.edit(embed=ready_embed)

        # Send notification with play options to Discord channel
        if channel:
            suno_link = f"\n🔗 [Open on Suno]({suno_url})" if suno_url else ""
            embed = discord.Embed(
                title="🎵 New Track Created!",
                description=(
                    f"**{title}**{suno_link}\n"
                    f"Use `/play` to start the radio or the "
                    f"track will auto-play if radio is active."
                ),
                color=0x9B59B6,
            )
            if style_tags:
                embed.add_field(name="Style", value=f"```{style_tags[:200]}```", inline=False)
            embed.set_footer(text="Gengar DJ • Suno R2 Engine")
            await channel.send(embed=embed)

        # Optionally play in voice channel immediately
        if play_in_vc and guild_id in self.bot.radio_states:
            state = self.bot.radio_states[guild_id]
            if state.vc and state.vc.is_connected():
                await state.queue_song(file_key, title)

        return web.json_response({"status": "ok", "song_id": entry["id"], "key": file_key})

    async def handle_get_playlist(self, request):
        """Fetch the current playlist from R2."""
        playlist = await asyncio.to_thread(self._load_r2_playlist_sync)
        return web.json_response(playlist)

    async def handle_add_to_playlist(self, request):
        """Add manual metadata / existing R2 file to R2 playlist."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        file_key = data.get("file")
        title = data.get("title", os.path.basename(file_key or "unknown"))
        if not file_key:
            return web.json_response({"error": "file (key) required"}, status=400)

        entry = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "file": file_key,
            "source": "manual",
            "created_at": asyncio.get_event_loop().time(),
        }
        await asyncio.to_thread(self._append_to_r2_playlist_sync, entry)
        return web.json_response({"status": "ok", "id": entry["id"]})

    async def handle_remove_from_playlist(self, request):
        """Remove metadata entry from playlist.json on R2."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        song_id = data.get("id")
        if not song_id:
            return web.json_response({"error": "id required"}, status=400)

        await asyncio.to_thread(self._remove_from_r2_playlist_sync, song_id)
        return web.json_response({"status": "ok"})

    # ─── sync-to-thread R2 operations ─────────────────────────────

    def _load_r2_playlist_sync(self) -> list:
        try:
            res = self.s3_client.get_object(
                Bucket=self.config.r2_bucket_name,
                Key="playlist.json"
            )
            return json.loads(res["Body"].read().decode("utf-8"))
        except self.s3_client.exceptions.NoSuchKey:
            return []
        except Exception as e:
            logger.warning("Error reading playlist.json from R2: %s", e)
            return []

    def _append_to_r2_playlist_sync(self, entry: dict):
        playlist = self._load_r2_playlist_sync()
        playlist.append(entry)
        self._save_r2_playlist_sync(playlist)

    def _remove_from_r2_playlist_sync(self, song_id: str):
        playlist = self._load_r2_playlist_sync()
        playlist[:] = [s for s in playlist if s.get("id") != song_id]
        self._save_r2_playlist_sync(playlist)

    def _save_r2_playlist_sync(self, playlist: list):
        try:
            self.s3_client.put_object(
                Bucket=self.config.r2_bucket_name,
                Key="playlist.json",
                Body=json.dumps(playlist, indent=2).encode("utf-8"),
                ContentType="application/json"
            )
            logger.info("Saved updated playlist.json to Cloudflare R2")
        except Exception as e:
            logger.error("Failed to write playlist.json to R2: %s", e)


# Make discord available for embed creation
import discord
