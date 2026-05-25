"""Gengar DJ — Audio player and silence detection engine.

Uses discord.py's VoiceClient with AudioSink for real-time
voice energy monitoring. When silence exceeds the threshold,
the player starts shuffling through the lofi playlist.
"""

import asyncio
import json
import logging
import math
import os
import random
import time
from pathlib import Path

import discord
import numpy as np

logger = logging.getLogger("gengar_dj.player")


class SilenceSink(discord.AudioSink):
    """Audio sink that detects voice energy in real-time.

    Fires callbacks when someone speaks or silence starts.
    """

    def __init__(self, on_voice_activity=None, on_silence=None, threshold=0.015):
        self.on_voice_activity = on_voice_activity
        self.on_silence = on_silence
        self.threshold = threshold
        self.last_voice_time = time.time()
        self._speaking = False

    def write(self, data: bytes):
        """Called with PCM audio chunks. Analyze RMS energy."""
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

        # Start silence detection via AudioSink
        self.sink = SilenceSink(
            on_voice_activity=self._on_voice_activity,
            on_silence=self._on_silence_start,
            threshold=0.015,
        )
        self.vc.listen(self.sink)

        # Load playlist
        self._load_queue()

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
                self.vc.stop_listening() if self.vc else None
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
        return self.active  # call start_radio separately

    async def queue_song(self, file_path: str, title: str):
        """Queue a new song immediately (for /create responses)."""
        entry = {"file": file_path, "title": title}
        self.queue.append(entry)
        if not self.playing and self.vc and self.vc.is_connected():
            await self._play_next()

    def set_volume(self, vol: float):
        self.volume = max(0.0, min(2.0, vol))
        if self.vc and self.vc.source:
            self.vc.source.volume = self.volume

    # ─── internal ───────────────────────────────────────────────

    def _load_queue(self):
        """Load all songs from the songs directory into the queue."""
        songs_dir = Path(self.bot.config.songs_dir)
        playlist_file = Path(self.bot.config.playlist_file)

        # Load playlist entries first
        entries = []
        if playlist_file.exists():
            try:
                data = json.loads(playlist_file.read_text())
                for entry in data:
                    fp = entry.get("file", "")
                    if os.path.isfile(fp):
                        entries.append(entry)
                    else:
                        logger.warning("Playlist entry missing file: %s", fp)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not load playlist: %s", e)

        # Fall back to scanning songs directory
        if not entries and songs_dir.exists():
            for f in sorted(songs_dir.iterdir()):
                if f.suffix.lower() in (".mp3", ".ogg", ".wav", ".flac"):
                    entries.append({
                        "id": f.stem,
                        "title": f.stem,
                        "file": str(f),
                        "source": "local",
                    })

        # Apply genre filter
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
            "Loaded %d songs into queue (genre filter: %s)",
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
        pass  # handled by the main loop

    async def _play_next(self):
        """Play the next song from the queue."""
        if not self.queue:
            self._load_queue()
        if not self.queue:
            logger.warning("No songs in queue for guild %d", self.guild_id)
            return

        self.playing = True
        entry = self.queue.pop(0)
        # Re-queue for cycling (unless it was a fresh /create song)
        if entry.get("source") != "suno_fresh":
            self.queue.append(entry)

        self.current_song = entry
        file_path = entry["file"]

        if not os.path.isfile(file_path):
            logger.warning("Song file not found: %s", file_path)
            self.playing = False
            return

        try:
            source = discord.FFmpegPCMAudio(
                file_path,
                options="-filter:a loudnorm",
                before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            )
            volume_source = discord.PCMVolumeTransformer(source, volume=self.volume)

            def after_playing(error):
                if error:
                    logger.error("Playback error: %s", error)
                # Schedule next track
                coro = self._on_track_end()
                asyncio.run_coroutine_threadsafe(coro, asyncio.get_event_loop())

            self.vc.play(volume_source, after=after_playing)
            logger.info("Now playing: %s", entry["title"])

        except Exception as e:
            logger.error("Failed to play %s: %s", file_path, e)
            self.playing = False

    async def _on_track_end(self):
        """Called when a track finishes playing."""
        self.current_song = None
        if self.active and self.vc and self.vc.is_connected():
            # Check if voice activity happened during playback
            silence_duration = time.time() - self.last_activity
            if silence_duration >= 2:
                await self._play_next()
            else:
                self.playing = False
