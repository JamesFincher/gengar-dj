"""Gengar DJ configuration — loaded from environment with .env fallback."""

import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Config:
    """Central config loaded from environment variables.

    All settings can be overridden with env vars. Loads .env from the
    project root if it exists.
    """

    # Discord
    discord_bot_token: str

    # Hermes webhook for /create song routing
    hermes_webhook_url: str
    hermes_webhook_secret: str

    # Internal HTTP API server
    bot_api_host: str = "0.0.0.0"
    bot_api_port: int = 8080
    bot_callback_url: str = "http://localhost:8080"

    # Song library
    songs_dir: str = "/data/songs"
    playlist_file: str = "/data/playlist.json"

    # Silence detection
    silence_threshold: int = 25  # seconds before radio starts
    fade_duration: int = 3       # seconds for crossfade

    # Logging
    log_level: str = "INFO"

    def __init__(self):
        # Load .env from project root
        self._load_dotenv()

        self.discord_bot_token = self._req("DISCORD_BOT_TOKEN")
        self.hermes_webhook_url = os.environ.get(
            "HERMES_WEBHOOK_URL",
            "http://gengar-claw-01.local:8644/webhook/gengar-dj-create",
        )
        self.hermes_webhook_secret = os.environ.get(
            "HERMES_WEBHOOK_SECRET", ""
        )
        self.bot_api_host = os.environ.get("BOT_API_HOST", "0.0.0.0")
        self.bot_api_port = int(os.environ.get("BOT_API_PORT", "8080"))
        self.bot_callback_url = os.environ.get(
            "BOT_CALLBACK_URL",
            "http://gengar-dj-bot.gengar-lab.svc.cluster.local:8080",
        )
        self.songs_dir = os.environ.get("SONGS_DIR", "/data/songs")
        self.playlist_file = os.environ.get(
            "PLAYLIST_FILE", "/data/playlist.json"
        )
        self.silence_threshold = int(
            os.environ.get("SILENCE_THRESHOLD", "25")
        )
        self.fade_duration = int(os.environ.get("FADE_DURATION", "3"))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")

    @staticmethod
    def _req(key: str) -> str:
        val = os.environ.get(key)
        if not val:
            raise RuntimeError(
                f"Required environment variable {key} is not set"
            )
        return val

    def _load_dotenv(self):
        """Load a .env file from the project root (one directory up from bot/)."""
        # Walk up to find .env
        candidates = [
            Path.cwd() / ".env",
            Path(__file__).parent.parent / ".env",
            Path(__file__).parent / ".env",
        ]
        for p in candidates:
            if p.exists():
                logger.info("Loading env from %s", p)
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    if key not in os.environ:
                        os.environ[key] = val
                break

    @property
    def log_level_int(self) -> int:
        return getattr(logging, self.log_level.upper(), logging.INFO)
