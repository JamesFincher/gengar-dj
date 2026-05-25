"""Gengar DJ — Main entry point.

A Discord bot that runs a silence-activated lofi radio in voice channels.
All tracks and metadata are preserved directly on Cloudflare R2.
"""

import asyncio
import logging
import signal
import sys

import discord
from discord.ext import commands
import boto3
from botocore.config import Config as BotoConfig

from .config import Config
from .api import APIServer

logger = logging.getLogger("gengar_dj")


class GengarDJ(commands.Bot):
    """The Gengar DJ Discord bot."""

    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="📻 for silences…",
            ),
        )

        self.config = config
        self.api_server: APIServer | None = None

        # Track radio state per guild
        # {guild_id: RadioState}
        self.radio_states: dict[int, "RadioState"] = {}

        # Initialize the global Cloudflare R2 Client
        self._init_r2_client()

    def _init_r2_client(self):
        """Initialize the boto3 Cloudflare R2 client."""
        endpoint = f"https://{self.config.r2_account_id}.r2.cloudflarestorage.com"
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=self.config.r2_access_key_id,
            aws_secret_access_key=self.config.r2_secret_access_key,
            config=BotoConfig(signature_version="s3v4"),
        )
        logger.info("Central Cloudflare R2 Client initialized successfully.")

    async def setup_hook(self):
        await self.load_extension("bot.cogs.radio")
        await self.load_extension("bot.cogs.music")
        await self.load_extension("bot.cogs.admin")
        logger.info("Cogs loaded successfully")

    async def on_ready(self):
        logger.info("═══════════════════════════════════")
        logger.info("  Gengar DJ is online!")
        logger.info(f"  User:       {self.user} (ID: {self.user.id})")
        logger.info(f"  Guilds:     {len(self.guilds)}")
        logger.info(f"  R2 Bucket:  {self.config.r2_bucket_name}")
        logger.info("═══════════════════════════════════")

        # Start the internal HTTP API for song creation callbacks
        self.api_server = APIServer(self)
        asyncio.create_task(self.api_server.start())

    async def close(self):
        if self.api_server:
            await self.api_server.stop()
        # Disconnect from all voice channels
        for guild_id, state in list(self.radio_states.items()):
            if state.vc and state.vc.is_connected():
                await state.vc.disconnect(force=True)
        await super().close()


def setup_logging(config: Config):
    logging.basicConfig(
        level=config.log_level_int,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # quiet noisy libs
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def main():
    config = Config()
    setup_logging(config)

    bot = GengarDJ(config)

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(bot.close()))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await bot.start(config.discord_bot_token)
    except KeyboardInterrupt:
        await bot.close()
    finally:
        logger.info("Gengar DJ shut down.")


if __name__ == "__main__":
    asyncio.run(main())
