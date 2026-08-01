"""AI chat bot for Chatto, using an OpenAI-compatible chat/completions endpoint.

Configure via environment variables (see README section "AI bot"):

    CHATTO_INSTANCE   = https://chatto.moonchan.xyz
    CHATTO_EMAIL      = <bot login email>   (or CHATTO_TOKEN)
    CHATTO_PASSWORD   = <bot password>
    OPENAI_BASE_URL   = https://api.openai.com/v1   (any OpenAI-compatible endpoint)
    OPENAI_API_KEY    = <key>
    OPENAI_MODEL      = gpt-4o-mini
    OPENAI_SYSTEM_PROMPT = <optional system prompt>

The bot replies when a message starts with the AI prefix (default `!ai`) or
mentions the bot by login (`@<login>`).
"""

from __future__ import annotations

import os
import re

import httpx

from chatto_bot import Bot, Context
from chatto_bot.event import on_event


SYSTEM_PROMPT = os.environ.get(
    "OPENAI_SYSTEM_PROMPT",
    "You are a helpful assistant in a team chat. Answer concisely.",
)

AI_PREFIX = os.environ.get("AI_PREFIX", "!ai")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


async def ask_llm(prompt: str) -> str:
    """Call an OpenAI-compatible /chat/completions endpoint."""
    if not API_KEY:
        return "AI bot is not configured: OPENAI_API_KEY is unset."
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return f"Unexpected LLM response: {data}"


def extract_prompt(bot: Bot, ctx: Context, body: str) -> str | None:
    """Return the prompt if the message addresses the bot, else None."""
    stripped = body.lstrip()
    if not stripped:
        return None

    if stripped.startswith(AI_PREFIX):
        rest = stripped[len(AI_PREFIX):].lstrip()
        return rest or None

    if bot.user and bot.user.login:
        if re.match(rf"@(?:{re.escape(bot.user.login)})(?=\s|$)", stripped, re.IGNORECASE):
            return re.sub(
                rf"@(?:{re.escape(bot.user.login)})(?=\s|$)", "", stripped, flags=re.IGNORECASE
            ).strip() or None
    return None


@on_event("message_posted")
async def on_message(ctx: Context) -> None:
    if not ctx.body:
        return

    prompt = extract_prompt(ctx.bot, ctx, ctx.body)
    if not prompt:
        return

    try:
        answer = await ask_llm(prompt)
    except Exception as e:  # network, HTTP, JSON...
        answer = f"AI error: {e}"
    await ctx.reply(answer)


def main() -> None:
    bot = Bot(prefix="!", dms=True)
    bot.add_event_handler(on_message)
    bot.run()


if __name__ == "__main__":
    main()
