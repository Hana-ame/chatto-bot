"""Run the Chatto AI bot (loads plugins.ai).

Reads .env / environment variables for Chatto credentials and the
OpenAI-compatible endpoint. See README "AI bot".
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chatto_bot import Bot


async def _main() -> None:
    bot = Bot(prefix="!", dms=True)
    await bot.load_extension("plugins.ai")
    bot.run()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
