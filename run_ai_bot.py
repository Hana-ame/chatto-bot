"""Run the Chatto AI bot.

Reads .env / environment variables for Chatto credentials and the
OpenAI-compatible endpoint, and loads the `plugins.ai` extension from
chatto-bot.yaml. On startup the bot joins every room visible to it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chatto_bot import Bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def _join_visible_rooms(bot: Bot) -> None:
    try:
        rooms = await bot.client.list_rooms()
    except Exception:
        logging.getLogger(__name__).exception("Failed to list rooms")
        return
    for rws in rooms:
        room = getattr(rws, "room", None)
        if room is None or not getattr(room, "id", ""):
            continue
        try:
            await bot.client.join_room(room.id)
            logging.getLogger(__name__).info("Joined room: %s (%s)", room.name, room.id)
        except Exception:
            logging.getLogger(__name__).debug(
                "Could not join room %s (may already be a member)", room.id, exc_info=True
            )


async def _main() -> None:
    bot = Bot(config_path="chatto-bot.yaml", prefix="!", dms=True)
    loop = asyncio.get_running_loop()

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, stop.set)

    await bot.start()
    await _join_visible_rooms(bot)
    await stop.wait()
    await bot.close()


if __name__ == "__main__":
    asyncio.run(_main())
