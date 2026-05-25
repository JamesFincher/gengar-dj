"""Internal HTTP API server for Gengar DJ.

Handles song creation callbacks from Hermes/Gengar and file uploads.
Runs as an async aiohttp server alongside the Discord bot.
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

logger = logging.getLogger("gengar_dj.api")


class APIServer:
    """aiohttp server that receives song creation callbacks from Hermes."""

    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self):
        self._app.router.add_post("/api/callback/song", self.handle_song_callback)
        self._app.router.add_post("/api/upload", self.handle_upload)
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
        """Receive completed song from Hermes/Gengar after /create.

        Expected JSON payload:
        {
            "guild_id": int,
            "channel_id": int,
            "user_id": int,
            "title": str,
            "file_path": str,       // path to MP3 file on shared volume or
            "download_url": str,    // URL the bot can fetch to download
            "play_in_vc": bool,
            "style_tags": str (optional)
        }
        """
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        title = data.get("title", "Untitled")
        file_path = data.get("file_path")
        download_url = data.get("download_url")
        play_in_vc = data.get("play_in_vc", False)
        style_tags = data.get("style_tags", "")

        if not guild_id or not channel_id:
            return web.json_response({"error": "guild_id and channel_id required"}, status=400)

        # Determine song path
        song_path = file_path
        if download_url and not song_path:
            # Download the file
            song_path = await self._download_song(download_url, title)
            if not song_path:
                return web.json_response({"error": "failed to download song"}, status=502)

        if not song_path or not os.path.isfile(song_path):
            return web.json_response({"error": f"song file not found: {song_path}"}, status=400)

        # Copy or move to songs dir
        songs_dir = Path(self.config.songs_dir)
        songs_dir.mkdir(parents=True, exist_ok=True)
        dest = songs_dir / f"{uuid.uuid4().hex[:8]} - {self._sanitize_filename(title)}.mp3"
        import shutil
        shutil.copy2(song_path, dest)
        logger.info("Copied new song to library: %s", dest)

        # Add to playlist
        playlist = self._load_playlist()
        entry = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "file": str(dest),
            "style_tags": style_tags,
            "source": "suno",
            "created_at": asyncio.get_event_loop().time(),
        }
        playlist.append(entry)
        self._save_playlist(playlist)

        # Send message to Discord channel
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if channel:
            embed = discord.Embed(
                title="🎵 New Song Created!",
                description=f"**{title}**",
                color=0x9B59B6,
            )
            if style_tags:
                embed.add_field(name="Style", value=f"```{style_tags[:200]}```", inline=False)
            embed.set_footer(text="Gengar DJ • Suno Generator")
            await channel.send(embed=embed)

        # Optionally play in voice channel
        if play_in_vc and guild_id in self.bot.radio_states:
            state = self.bot.radio_states[guild_id]
            if state.vc and state.vc.is_connected():
                await state.queue_song(str(dest), title)

        return web.json_response({"status": "ok", "song_id": entry["id"], "file": str(dest)})

    async def handle_upload(self, request):
        """Accept MP3 file upload from Hermes webhook."""
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != "file":
            return web.json_response({"error": "missing 'file' field"}, status=400)

        songs_dir = Path(self.config.songs_dir)
        songs_dir.mkdir(parents=True, exist_ok=True)
        filename = field.filename or f"{uuid.uuid4().hex}.mp3"
        dest = songs_dir / self._sanitize_filename(filename)

        with open(dest, "wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)

        logger.info("Uploaded song: %s (%d bytes)", dest, dest.stat().st_size)
        return web.json_response({"status": "ok", "file": str(dest)})

    async def handle_get_playlist(self, request):
        playlist = self._load_playlist()
        return web.json_response(playlist)

    async def handle_add_to_playlist(self, request):
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        file_path = data.get("file")
        title = data.get("title", os.path.basename(file_path or "unknown"))
        if not file_path or not os.path.isfile(file_path):
            return web.json_response({"error": "file not found"}, status=400)

        playlist = self._load_playlist()
        entry = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "file": file_path,
            "source": "manual",
            "created_at": asyncio.get_event_loop().time(),
        }
        playlist.append(entry)
        self._save_playlist(playlist)
        return web.json_response({"status": "ok", "id": entry["id"]})

    async def handle_remove_from_playlist(self, request):
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)

        song_id = data.get("id")
        if not song_id:
            return web.json_response({"error": "id required"}, status=400)

        playlist = self._load_playlist()
        playlist[:] = [s for s in playlist if s.get("id") != song_id]
        self._save_playlist(playlist)
        return web.json_response({"status": "ok"})

    # ─── helpers ────────────────────────────────────────────────

    def _load_playlist(self) -> list:
        path = Path(self.config.playlist_file)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt playlist file, starting fresh")
        return []

    def _save_playlist(self, playlist: list):
        path = Path(self.config.playlist_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(playlist, indent=2))

    def _sanitize_filename(self, name: str) -> str:
        """Strip or replace characters that are problematic in filenames."""
        keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
        return "".join(c if c in keep else "_" for c in name).strip("._")

    async def _download_song(self, url: str, title: str) -> str | None:
        """Download a song from a URL into the songs directory."""
        songs_dir = Path(self.config.songs_dir)
        songs_dir.mkdir(parents=True, exist_ok=True)
        dest = songs_dir / f"{uuid.uuid4().hex[:8]} - {self._sanitize_filename(title)}.mp3"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status != 200:
                        logger.error("Download failed: HTTP %d for %s", resp.status, url)
                        return None
                    with open(dest, "wb") as f:
                        while True:
                            chunk = await resp.content.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
            logger.info("Downloaded song: %s", dest)
            return str(dest)
        except Exception as e:
            logger.error("Download error for %s: %s", url, e)
            # Clean up partial download
            if dest.exists():
                dest.unlink()
            return None


# Make discord available for embed creation
import discord
