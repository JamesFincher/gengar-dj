"""Gengar DJ — Audio player and silence detection engine.

Streams lofi tracks directly from Cloudflare R2 bucket
when the voice channel goes completely quiet.
"""

import asyncio
import json
import logging
import math
import os
import random
import time
from urllib.parse import quote

import discord
import discord.sinks
import numpy as np
import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger("gengar_dj.player")


class SilenceSink(discord.sinks.PCMSink):
    """Audio sink that detects voice energy in real-time.

    Fires callbacks when someone speaks or silence starts.
    """

    def __init__(self, on_voice_activity=None, on_silence=None, threshold=0.015):
        super().__init__()
        self.on_voice_activity = on_voice_activity
        self.on_silence = on_silence
        self.threshold = threshold
        self.last_voice_time = time.time()
        self._speaking = False

    def write(self, data, user):
        """Called with PCM audio chunks. Analyze RMS energy."""
        super().write(data, user)
        try:
            # Convert PCM s16le bytes to numpy array
            frame = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            if len(frame) == 0:
                return

            rms = math.sqrt(np.mean(frame ** 2))
            energy = rms / 32768.0  # normalize to 0.0-1.0

            if energy > self.threshold:
                self.last_voice_time = time.time()
                if not self._speaking:
                    self._speaking = True
                    if self.on_voice_activity:
                        asyncio.run_coroutine_threadsafe(
                            self.on_voice_activity(), self._loop
                        )
            else:
                if self._speaking and (time.time() - self.last_voice_time) > 3.0:
                    self._speaking = False
                    if self.on_silence:
                        asyncio.run_coroutine_threadsafe(
                            self.on_silence(), self._loop
                        )
        except Exception as e:
            logger.warning("SilenceSink error: %s", e)

    @property
    def _loop(self):
        """Get the event loop for scheduling coroutines."""
        return asyncio.get_event_loop()


class RadioState:
    """Per-guild state tracking for radio functionality."""

    def __init__(self, guild_id: int, bot):
        self.guild_id = guild_id
        self.bot = bot
        self.vc: discord.VoiceClient | None = None
        self.sink: SilenceSink | None = None
        self.active = False  # is the radio enabled?
        self.playing = False  # is music currently playing?

        # Song queue
        self.queue: list[dict] = []
        self.current_song: dict | None = None
        self.shuffle = True
        self.genre_filter: str | None = None

        # Silence detection
        self.silence_start: float | None = None
        self.last_activity: float = time.time()
        self._radio_task: asyncio.Task | None = None

        # Volume (0.0 - 2.0)
        self.volume: float = 0.6

        # Cloudflare R2 client
        self.s3_client = bot.s3_client

    async def start_radio(self, voice_channel: discord.VoiceChannel):
        """Join a VC and begin silence monitoring."""
        try:
            self.vc = await voice_channel.connect(timeout=30, reconnect=True)
            logger.info(
                "Joined VC %s in guild %s", voice_channel.name, self.guild_id
            )
        except Exception as e:
            logger.error("Failed to join VC: %s", e)
            return False

        self.active = True
        self.last_activity = time.time()

        # Start silence detection via PCMSink
        self.sink = SilenceSink(
            on_voice_activity=self._on_voice_activity,
            on_silence=self._on_silence_start,
            threshold=0.015,
        )
        
        async def finished_callback(sink, *args):
            pass

        self.vc.start_recording(self.sink, finished_callback)

        # Load playlist from R2
        await self._load_queue()

        # Start the radio loop
        self._radio_task = asyncio.create_task(self._radio_loop())

        return True

    async def stop_radio(self):
        """Stop the radio and leave VC."""
        self.active = False
        self.playing = False

        if self._radio_task:
            self._radio_task.cancel()
            self._radio_task = None

        if self.sink:
            try:
                if self.vc and hasattr(self.vc, "recording") and self.vc.recording:
                    self.vc.stop_recording()
            except Exception:
                pass
            self.sink = None

        if self.vc and self.vc.is_connected():
            await self.vc.disconnect(force=True)
            self.vc = None

        self.current_song = None

    async def toggle(self) -> bool:
        """Toggle radio on/off. Returns new state."""
        if self.active:
            await self.stop_radio()
            return False
        return self.active

    async def queue_song(self, file_key: str, title: str):
        """Queue a new song immediately (for /create or /dj-spinup responses)."""
        entry = {
            "file": file_key,
            "title": title,
            "source": "suno_fresh"
        }
        self.queue.insert(0, entry)  # Insert at the front to play next
        if not self.playing and self.vc and self.vc.is_connected():
            await self._play_next()

    def set_volume(self, vol: float):
        self.volume = max(0.0, min(2.0, vol))
        if self.vc and self.vc.source:
            self.vc.source.volume = self.volume

    # ─── internal ───────────────────────────────────────────────

    async def _load_queue(self):
        """Load songs from Cloudflare R2 bucket."""
        await asyncio.to_thread(self._load_queue_sync)

    def _load_queue_sync(self):
        """Synchronous part of queue loading (run in thread)."""
        logger.info("Syncing lofi library from Cloudflare R2 bucket: %s", self.bot.config.r2_bucket_name)
        entries = []
        playlist_data = []

        try:
            res = self.s3_client.get_object(
                Bucket=self.bot.config.r2_bucket_name,
                Key="playlist.json"
            )
            playlist_data = json.loads(res["Body"].read().decode("utf-8"))
            logger.info("Loaded metadata playlist.json from R2 (%d items)", len(playlist_data))
        except self.s3_client.exceptions.NoSuchKey:
            logger.info("playlist.json not found in R2. Scanning bucket dynamically...")
        except Exception as e:
            logger.warning("Error reading playlist.json from R2: %s", e)

        metadata_map = {}
        for item in playlist_data:
            key = item.get("file") or item.get("key")
            if key:
                metadata_map[key] = item

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bot.config.r2_bucket_name):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.lower().endswith((".mp3", ".ogg", ".wav", ".flac")) and key != "playlist.json":
                        meta = metadata_map.get(key) or {}
                        title = meta.get("title") or os.path.splitext(os.path.basename(key))[0]
                        style_tags = meta.get("style_tags") or meta.get("tags") or ""

                        entries.append({
                            "id": key,
                            "title": title,
                            "file": key,
                            "style_tags": style_tags,
                            "source": meta.get("source") or "r2"
                        })
        except Exception as e:
            logger.error("Failed to list objects in Cloudflare R2: %s", e)

        if self.genre_filter:
            filtered = []
            for e in entries:
                tags = (e.get("style_tags", "") + " " + e.get("title", "")).lower()
                if self.genre_filter.lower() in tags:
                    filtered.append(e)
            entries = filtered

        self.queue = entries
        if self.shuffle:
            random.shuffle(self.queue)

        logger.info(
            "Loaded %d R2 tracks into rotation queue (filter: %s)",
            len(self.queue),
            self.genre_filter or "none",
        )

    async def _radio_loop(self):
        """Main loop: monitor silence threshold and play/pause."""
        while self.active:
            try:
                if not self.playing:
                    # Check how long since last voice activity
                    silence_duration = time.time() - self.last_activity
                    threshold = self.bot.config.silence_threshold

                    if silence_duration >= threshold and self.vc and self.vc.is_connected():
                        logger.info(
                            "Silence for %.1fs in guild %d — starting radio",
                            silence_duration,
                            self.guild_id,
                        )
                        await self._play_next()

                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Radio loop error (guild %d): %s", self.guild_id, e)
                await asyncio.sleep(5)

    async def _on_voice_activity(self):
        """Called when someone speaks in VC. Stop music immediately."""
        self.last_activity = time.time()
        if self.playing:
            logger.info("Voice detected in guild %d — pausing radio", self.guild_id)
            self.playing = False
            if self.vc and self.vc.is_playing():
                self.vc.stop()

    async def _on_silence_start(self):
        """Called when silence begins after speaking stopped."""
        pass

    async def _play_next(self):
        """Play the next song from the queue."""
        if not self.queue:
            await self._load_queue()
        if not self.queue:
            logger.warning("No songs in queue for guild %d", self.guild_id)
            return

        self.playing = True
        entry = self.queue.pop(0)
        
        # Re-queue for cycling (unless it was a fresh /create song)
        if entry.get("source") != "suno_fresh":
            self.queue.append(entry)

        self.current_song = entry
        key = entry["file"]

        # Generate streaming URL
        try:
            if self.bot.config.r2_public_url:
                encoded_key = quote(key)
                play_url = f"{self.bot.config.r2_public_url}/{encoded_key}"
            else:
                play_url = await asyncio.to_thread(
                    self.s3_client.generate_presigned_url,
                    "get_object",
                    Params={"Bucket": self.bot.config.r2_bucket_name, "Key": key},
                    ExpiresIn=3600,
                )
        except Exception as e:
            logger.error("Failed to generate streaming URL for key %s: %s", key, e)
            self.playing = False
            return

        try:
            source = discord.FFmpegPCMAudio(
                play_url,
                options="-filter:a loudnorm",
                before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            )
            volume_source = discord.PCMVolumeTransformer(source, volume=self.volume)

            def after_playing(error):
                if error:
                    logger.error("Playback error: %s", error)
                coro = self._on_track_end()
                asyncio.run_coroutine_threadsafe(coro, asyncio.get_event_loop())

            self.vc.play(volume_source, after=after_playing)
            logger.info("Now playing from R2: %s", entry["title"])

        except Exception as e:
            logger.error("Failed to play %s from R2: %s", key, e)
            self.playing = False

    async def _on_track_end(self):
        """Called when a track finishes playing."""
        self.current_song = None
        if self.active and self.vc and self.vc.is_connected():
            silence_duration = time.time() - self.last_activity
            if silence_duration >= 2:
                await self._play_next()
            else:
                self.playing = False
        else:
            self.playing = False
