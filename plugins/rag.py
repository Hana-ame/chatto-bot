"""RAG retrievers for the AI plugin.

Pluggable retrieval sources feeding extra context into `plugins.ai.ask_llm`:

    history   = recent messages of the current room (via Chatto timeline)
    websearch = SearXNG JSON API  (enable by setting SEARCH_URL)
    localmd   = *.md files under KNOWLEDGE_DIR, scored by keyword overlap

`!memo <content>` writes a note into `knowledge/memory.md` (explicit
write-back); everything in KNOWLEDGE_DIR is read back on every query, so
memoised notes are retrieved automatically.

Config (environment variables):

    KNOWLEDGE_DIR   = knowledge        (repo root; created if missing)
    HISTORY_LIMIT   = 20               (messages fetched from the room)
    SEARCH_URL      =                  (SearXNG base URL, e.g. https://searx.example; empty = disabled)
    SEARCH_RESULTS  = 3                (top web results injected)
    RAG_MAX_CHARS   = 6000             (cap on total injected context)
    EMBEDDING_URL   = https://api.siliconflow.cn/v1/embeddings   (OpenAI-compatible)
    EMBEDDING_API_KEY = <key>          (empty = keyword-only retrieval)
    EMBEDDING_MODEL = BAAI/bge-m3

Knowledge chunks are scored by keyword overlap plus (when configured)
embedding similarity; chunk embeddings are cached in
``knowledge/.embeddings.json`` and rebuilt lazily per chunk.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = os.environ.get("KNOWLEDGE_DIR", "knowledge")
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", "20"))
SEARCH_URL = os.environ.get("SEARCH_URL", "").rstrip("/")
SEARCH_RESULTS = int(os.environ.get("SEARCH_RESULTS", "3"))
RAG_MAX_CHARS = int(os.environ.get("RAG_MAX_CHARS", "6000"))
EMBEDDING_URL = os.environ.get(
    "EMBEDDING_URL", "https://api.siliconflow.cn/v1/embeddings"
).rstrip("/")
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")

_EMBED_CACHE = "knowledge/.embeddings.json"
_TOP_K = 5

_MEMO_FILE = "memory.md"

_STOP = re.compile(r"[!?。？！，,;；：:、\s]+")

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9]+")


def _fold(text: str) -> str:
    """Lowercased text stripped of whitespace and punctuation, for substring
    matching that ignores spacing (e.g. `用户 luminovoez` vs `用户luminovoez`)."""
    return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "", text.lower())


def _tokenize(text: str) -> set[str]:
    """Query-token set: ASCII words plus CJK bigrams (falling back to single
    CJK characters when there is only one). Words stay whole so English terms
    match exactly; Chinese is split into adjacent character bigrams so phrases
    like 生日/知识库 align between query and knowledge chunks."""
    units = _CJK.findall(text.lower()) or []
    words = _WORD.findall(text.lower())
    tokens: set[str] = set(words)
    if units:
        tokens.update(units)
        tokens.update(f"{a}{b}" for a, b in zip(units, units[1:]))
    return tokens


async def _history(ctx: Any) -> str:
    """Recent messages of the current room as conversational context."""
    room_id = ctx.room_id
    if not room_id:
        return ""
    try:
        page = await ctx.bot.client.get_room_events(room_id, limit=HISTORY_LIMIT)
    except Exception:
        logger.debug("history retrieval failed", exc_info=True)
        return ""

    names: dict[str, str] = {}
    actors = sorted(
        {e.actor_id for e in page.events if getattr(e, "actor_id", "")},
    )
    if actors:
        try:
            members = await ctx.bot.client.batch_get_users(actors)
            names = {
                m.user.id: (m.user.display_name or m.user.login or m.user.id)
                for m in members
                if getattr(m, "user", None)
            }
        except Exception:
            logger.debug("batch_get_users failed", exc_info=True)

    lines: list[str] = []
    for ev in page.events:
        oneof = getattr(ev, "event", None)
        if oneof is None or getattr(oneof, "field", None) != "message_posted":
            continue
        msg = getattr(oneof.value, "message", None)
        body = (getattr(msg, "body", "") or "").strip()
        if not body or body.startswith(("!", "@")):
            continue
        who = names.get(getattr(ev, "actor_id", ""), "someone")
        lines.append(f"{who}: {body}")
    return "\n".join(lines[-HISTORY_LIMIT:])


async def _websearch(query: str) -> str:
    """Top web results via a SearXNG JSON API endpoint."""
    if not SEARCH_URL:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"{SEARCH_URL}/search",
                params={"q": query, "format": "json"},
            )
            resp.raise_for_status()
            results = (resp.json().get("results") or [])[:SEARCH_RESULTS]
    except Exception:
        logger.debug("web search failed", exc_info=True)
        return ""

    lines: list[str] = []
    for r in results:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip()
        head = f"{title} ({url})" if title else url
        lines.append(f"{head}: {snippet}" if snippet else head)
    return "\n".join(lines)


def _chunk_md(path: Path) -> list[tuple[str, str]]:
    """Split a markdown file into (heading, body) chunks."""
    chunks: list[tuple[str, str]] = []
    heading = path.stem
    body: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", raw)
        if m:
            if body:
                chunks.append((heading, "\n".join(body).strip()))
            heading = m.group(1).strip()
            body = []
        else:
            body.append(raw)
    if body:
        chunks.append((heading, "\n".join(body).strip()))
    return [(h, b) for h, b in chunks if b]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _load_embed_cache() -> dict[str, list[float]]:
    try:
        data = json.loads(Path(_EMBED_CACHE).read_text(encoding="utf-8"))
        if data.get("model") == EMBEDDING_MODEL:
            return data.get("chunks") or {}
    except (OSError, ValueError):
        pass
    return {}


def _save_embed_cache(cache: dict[str, list[float]]) -> None:
    try:
        path = Path(_EMBED_CACHE)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"model": EMBEDDING_MODEL, "chunks": cache}),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        logger.debug("could not write embedding cache", exc_info=True)


async def _embed(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch of texts via an OpenAI-compatible /embeddings endpoint."""
    if not EMBEDDING_API_KEY or not texts:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                EMBEDDING_URL,
                headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}"},
                json={"model": EMBEDDING_MODEL, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            by_index = {item["index"]: item["embedding"] for item in data["data"]}
            return [by_index[i] for i in range(len(texts)) if i in by_index]
    except Exception:
        logger.exception("embedding request failed")
        return None


async def _semantic_scores(
    query: str, chunks: list[tuple[str, str]]
) -> list[float] | None:
    """Cosine similarity of the query against each chunk (with disk cache)."""
    if not EMBEDDING_API_KEY or not chunks:
        return None

    cache = _load_embed_cache()
    need: list[str] = []
    for key, _ in chunks:
        if key not in cache:
            need.append(key)

    if need:
        by_key = {key: text for key, text in chunks}
        vecs = await _embed([by_key[k] for k in need])
        if vecs is None:
            return None
        for key, vec in zip(need, vecs):
            cache[key] = vec
        _save_embed_cache(cache)

    qvec = await _embed([query])
    if qvec is None:
        return None
    qvec = qvec[0]
    return [_cosine(qvec, cache.get(key, [])) for key, _ in chunks]


async def _localmd(query: str) -> str:
    """Top knowledge-base chunks scored by keyword overlap + embeddings."""
    root = Path(KNOWLEDGE_DIR)
    if not root.is_dir():
        return ""

    chunks: list[tuple[str, str]] = []
    for path in sorted(root.glob("**/*.md")):
        try:
            rel = path.relative_to(root).as_posix()
            for heading, body in _chunk_md(path):
                text = f"{heading}\n{body}"
                key = f"{rel}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
                chunks.append((key, text))
        except OSError:
            logger.debug("could not read %s", path, exc_info=True)
    if not chunks:
        return ""

    query_tokens = _tokenize(query)
    kw_scores: list[float] = []
    for _, text in chunks:
        tokens = _tokenize(text)
        ratio = len(query_tokens & tokens) / max(len(query_tokens), 1)
        bonus = 1.0 if _fold(query) in _fold(text) else 0.0
        kw_scores.append(ratio + bonus)

    sem_scores = await _semantic_scores(query, chunks)

    merged: list[tuple[float, int]] = []
    for i, (_, text) in enumerate(chunks):
        score = kw_scores[i]
        if sem_scores is not None:
            score += 0.5 * sem_scores[i]
        if score > 0:
            merged.append((score, i))
    merged.sort(key=lambda s: (-s[0], len(chunks[s[1]][1])))

    top = []
    for _, i in merged[:_TOP_K]:
        text = chunks[i][1]
        if len(text) > 2000:
            text = text[:2000] + "…"
        top.append(text)
    return "\n\n".join(top)


async def retrieve_all(query: str, ctx: Any) -> str:
    """Gather context from all enabled sources, capped at RAG_MAX_CHARS."""
    history, web, kb = await asyncio.gather(
        _history(ctx), _websearch(query), _localmd(query)
    )

    sections: list[str] = []
    if history:
        sections.append(f"[本房间最近聊天]\n{history}")
    if kb:
        sections.append(f"[知识库参考]\n{kb}")
    if web:
        sections.append(f"[网络搜索结果]\n{web}")

    context = "\n\n".join(sections).strip()
    if len(context) > RAG_MAX_CHARS:
        context = context[:RAG_MAX_CHARS] + "…"
    return context


async def remember(content: str, ctx: Any) -> str:
    """Write a memo into the knowledge base (explicit write-back)."""
    content = content.strip()
    if not content:
        return "用法: !memo <要记住的内容>"

    root = Path(KNOWLEDGE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    memo = root / _MEMO_FILE

    text = memo.read_text(encoding="utf-8", errors="replace") if memo.exists() else ""
    if content in text:
        return "这条内容已经在知识库里了。"

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    line = f"- [{ts}] {content}"
    with memo.open("a", encoding="utf-8") as f:
        f.write(f"\n{line}\n")
    logger.info("memo written to %s", memo)
    return "记住了,已写入知识库 (knowledge/memory.md)。"
