"""AI chat plugin for Chatto — replies via an OpenAI-compatible endpoint.

Loaded as a ChattoBot extension (see `setup` below). Configure via
environment variables:

    OPENAI_BASE_URL    = https://api.openai.com/v1   (any OpenAI-compatible endpoint)
    OPENAI_API_KEY     = <key>
    OPENAI_MODEL       = gpt-4o-mini
    OPENAI_SYSTEM_PROMPT = <optional system prompt>
    AI_PREFIX          = !ai

The bot replies when a message starts with the AI prefix (default `!ai`) or
mentions the bot by login (`@<login> ...`).
"""

from __future__ import annotations

import logging
import os
import re

import httpx

from chatto_bot import Bot, Context

logger = logging.getLogger(__name__)

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


def extract_prompt(bot: Bot, body: str) -> str | None:
    """Return the prompt if the message addresses the bot, else None."""
    stripped = body.lstrip()
    if not stripped:
        return None

    if stripped.startswith(AI_PREFIX):
        return stripped[len(AI_PREFIX):].lstrip() or None

    if bot.user and bot.user.login:
        login = re.escape(bot.user.login)
        if re.match(rf"@(?:{login})(?=\s|$)", stripped, re.IGNORECASE):
            return re.sub(
                rf"@(?:{login})(?=\s|$)", "", stripped, flags=re.IGNORECASE
            ).strip() or None
    return None


async def on_message(ctx: Context) -> None:
    if not ctx.body:
        return

    prompt = extract_prompt(ctx.bot, ctx.body)
    if not prompt:
        return

    try:
        answer = await ask_llm(prompt)
    except Exception as e:  # network, HTTP, JSON...
        logger.exception("AI request failed")
        answer = f"AI error: {e}"
    await ctx.reply(answer)


async def setup(bot: Bot) -> None:
    bot.on_event("message_posted")(on_message)
    logger.info("AI plugin loaded (prefix=%r, model=%r)", AI_PREFIX, MODEL)
