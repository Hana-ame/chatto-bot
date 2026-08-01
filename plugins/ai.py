"""AI chat plugin for Chatto — replies via an OpenAI-compatible endpoint.

Loaded as a ChattoBot extension (see `setup` below). Configure via
environment variables:

    OPENAI_BASE_URL    = https://api.openai.com/v1   (any OpenAI-compatible endpoint)
    OPENAI_API_KEY     = <key>
    OPENAI_MODEL       = gpt-4o-mini
    OPENAI_SYSTEM_PROMPT = <optional system prompt>
    AI_PREFIX          = !ai

The bot replies when a message starts with the AI prefix (default `!ai`) or
mentions the bot by login (`@<login> ...`). Before answering, it retrieves
relevant context via `plugins.rag` (room history, local md knowledge base,
optional web search); `!memo <content>` writes notes into the knowledge base.
"""

from __future__ import annotations

import logging
import os
import re

import httpx

from chatto_bot import Bot, Context

from . import rag

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = os.environ.get(
    "OPENAI_SYSTEM_PROMPT",
    "You are a helpful assistant in a team chat. Answer concisely, and prefer "
    "the provided reference material over your own knowledge when they overlap.",
)

AI_PREFIX = os.environ.get("AI_PREFIX", "!ai")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


async def ask_llm(prompt: str, ctx: Context | None = None) -> str:
    """Call an OpenAI-compatible /chat/completions endpoint.

    When ``ctx`` is given, retrieval context from ``plugins.rag`` is injected
    as a second system message before the user prompt.
    """
    if not API_KEY:
        return "AI bot is not configured: OPENAI_API_KEY is unset."
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    if ctx is not None:
        context = await rag.retrieve_all(prompt, ctx)
        if context:
            messages.insert(
                1, {"role": "system", "content": f"参考资料:\n{context}"}
            )
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            BASE_URL,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": MODEL, "messages": messages},
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
        answer = await ask_llm(prompt, ctx)
    except Exception as e:  # network, HTTP, JSON...
        logger.exception("AI request failed")
        answer = f"AI error: {e}"
    await ctx.reply(answer)


async def on_memo(ctx: Context, content: str) -> None:
    """!memo <content> — write a note into the knowledge base."""
    await ctx.reply(await rag.remember(content, ctx))


async def setup(bot: Bot) -> None:
    bot.on_event("message_posted")(on_message)
    bot.command("memo", desc="把内容写入知识库: !memo <内容>")(on_memo)
    logger.info("AI plugin loaded (prefix=%r, model=%r)", AI_PREFIX, MODEL)
