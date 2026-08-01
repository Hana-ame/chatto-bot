"""Tests for plugins/rag.py — tokenization, chunking, scoring, memo write-back.

Network-dependent paths (_embed, _history, _websearch) are not exercised here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag


class TestTokenize:
    def test_ascii_words_stay_whole(self):
        assert "birthday" in rag._tokenize("what is your birthday")

    def test_chinese_bigrams(self):
        tokens = rag._tokenize("生日是哪天")
        assert "生日" in tokens
        assert "是哪" in tokens
        assert "哪天" in tokens

    def test_single_cjk_char(self):
        tokens = rag._tokenize("我")
        assert "我" in tokens

    def test_mixed_cn_en(self):
        tokens = rag._tokenize("用户luminovoez")
        assert "luminovoez" in tokens
        assert "用户" in tokens


class TestFold:
    def test_strips_punctuation_and_spaces(self):
        assert rag._fold("用户 luminovoez 的生日?") == rag._fold("用户luminovoez的生日")

    def test_lowercases(self):
        assert rag._fold("AI-Bot") == rag._fold("ai-bot")


class TestCosine:
    def test_identical_vectors(self):
        assert rag._cosine([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert rag._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_safe(self):
        assert rag._cosine([], []) == 0.0
        assert rag._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestChunkMd:
    def test_splits_on_headings(self):
        path = Path("/tmp/rag_test_split.md")
        path.write_text("# 甲\n第一段\n## 乙\n第二段\n", encoding="utf-8")
        try:
            chunks = rag._chunk_md(path)
            assert chunks == [("甲", "第一段"), ("乙", "第二段")]
        finally:
            path.unlink(missing_ok=True)

    def test_no_headings_uses_filename(self):
        path = Path("/tmp/rag_test_plain.md")
        path.write_text("只有正文\n", encoding="utf-8")
        try:
            assert rag._chunk_md(path) == [("rag_test_plain", "只有正文")]
        finally:
            path.unlink(missing_ok=True)


class TestLocalMd:
    @pytest.fixture(autouse=True)
    def _isolate_knowledge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "KNOWLEDGE_DIR", str(tmp_path))
        monkeypatch.setattr(rag, "_EMBED_CACHE", str(tmp_path / ".embeddings.json"))
        (tmp_path / "notes.md").write_text(
            "# 团队约定\nluminovoez 的生日是 8月2日\n", encoding="utf-8"
        )

    async def test_keyword_hit(self):
        result = await rag._localmd("生日是哪天")
        assert "8月2日" in result

    async def test_semantic_hit_without_key(self):
        # same-script query, no API key configured: keyword overlap still works
        result = await rag._localmd("luminovoez 生日")
        assert "8月2日" in result

    async def test_empty_knowledge_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "KNOWLEDGE_DIR", str(tmp_path / "missing"))
        assert await rag._localmd("anything") == ""


class TestRemember:
    @pytest.fixture(autouse=True)
    def _isolate_knowledge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "KNOWLEDGE_DIR", str(tmp_path))

    async def test_writes_memo(self, tmp_path):
        msg = await rag.remember("项目代号是 Moonchan", ctx=None)
        assert "记住了" in msg
        content = (tmp_path / "memory.md").read_text(encoding="utf-8")
        assert "项目代号是 Moonchan" in content

    async def test_dedup(self, tmp_path):
        await rag.remember("唯一内容 XYZ", ctx=None)
        msg = await rag.remember("唯一内容 XYZ", ctx=None)
        assert "已经" in msg
        assert (tmp_path / "memory.md").read_text(encoding="utf-8").count("唯一内容 XYZ") == 1

    async def test_empty_rejected(self):
        assert "用法" in await rag.remember("   ", ctx=None)


class TestEmbedCache:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "_EMBED_CACHE", str(tmp_path / ".embeddings.json"))
        rag._save_embed_cache({"a:1": [0.1, 0.2]})
        assert rag._load_embed_cache() == {"a:1": [0.1, 0.2]}

    def test_model_mismatch_discards(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag, "_EMBED_CACHE", str(tmp_path / ".embeddings.json"))
        monkeypatch.setattr(rag, "EMBEDDING_MODEL", "other-model")
        (tmp_path / ".embeddings.json").write_text(
            '{"model": "old-model", "chunks": {"k": [1.0]}}', encoding="utf-8"
        )
        assert rag._load_embed_cache() == {}
