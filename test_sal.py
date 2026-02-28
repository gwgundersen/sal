"""Unit tests for sal — Anthropic API calls are mocked throughout."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sal.core as core
from sal.mcp import List, Read, Search


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp(tmp_path):
    """Patch core.WS to a fresh temp directory."""
    with patch.object(core, "WS", tmp_path):
        yield tmp_path


def mock_client(card: dict) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(card))]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


# ── _read_file ─────────────────────────────────────────────────────────────────

def test_read_text_file(tmp):
    f = tmp / "doc.txt"
    f.write_text("hello world")
    assert core._read_file(f) == "hello world"


def test_read_pdf_via_fitz():
    page = MagicMock()
    page.get_text.return_value = "page content"
    with patch("sal.core.fitz.open", return_value=[page]):
        result = core._read_file(Path("test.pdf"))
    assert result == "page content"


# ── _resources ────────────────────────────────────────────────────────────────

def test_resources_lists_supported_extensions(tmp):
    (tmp / "doc.pdf").touch()
    (tmp / "notes.md").touch()
    (tmp / "ignore.xyz").touch()
    names = [f.name for f in core._resources()]
    assert "doc.pdf" in names
    assert "notes.md" in names
    assert "ignore.xyz" not in names


def test_resources_empty_when_no_matching_files(tmp):
    (tmp / "ignore.xyz").touch()
    assert core._resources() == []


# ── _index_one ────────────────────────────────────────────────────────────────

def test_index_one_parses_json(tmp):
    card = {"title": "Test Doc", "type": "paper", "topics": ["vol"], "summary": "A test.",
            "key_terms": ["volatility"], "prerequisites": ["calculus"], "key_results": ["BS formula"]}
    f = tmp / "doc.txt"
    f.write_text("some content")
    result = core._index_one(f, mock_client(card))
    assert result["title"] == "Test Doc"
    assert result["key_terms"] == ["volatility"]
    assert result["prerequisites"] == ["calculus"]
    assert result["key_results"] == ["BS formula"]
    assert "path" in result


def test_index_one_strips_code_fences(tmp):
    card = {"title": "Fenced", "type": "paper", "topics": [], "summary": "",
            "key_terms": [], "prerequisites": [], "key_results": []}
    fenced = f"```json\n{json.dumps(card)}\n```"
    response = MagicMock()
    response.content = [MagicMock(text=fenced)]
    client = MagicMock()
    client.messages.create.return_value = response
    f = tmp / "doc.txt"
    f.write_text("content")
    result = core._index_one(f, client)
    assert result["title"] == "Fenced"


# ── Tools ─────────────────────────────────────────────────────────────────────

def test_list_returns_cards():
    cards = [{"title": "My Paper", "path": "paper.pdf", "type": "paper",
              "topics": ["vol"], "summary": "About vol.", "sections": []}]
    with patch.object(core, "CARDS", cards):
        result = json.loads(List())
    assert result["documents"] == cards


def test_read_not_found(tmp):
    result = json.loads(Read("missing.txt"))
    assert "error" in result


def test_read_returns_content(tmp):
    (tmp / ".sal").mkdir()
    db = core._open_db()
    db.execute("INSERT INTO docs(path, body) VALUES (?, ?)", ("doc.txt", "hello"))
    db.commit(); db.close()
    (tmp / "doc.txt").write_text("hello")
    result = json.loads(Read("doc.txt"))
    assert result["content"] == "hello"


def test_search_finds_match(tmp):
    (tmp / ".sal").mkdir()
    db = core._open_db()
    db.execute("INSERT INTO docs(path, body) VALUES (?, ?)",
               ("notes.md", "local volatility is important\nother stuff"))
    db.commit(); db.close()
    result = json.loads(Search("local volatility"))
    assert len(result["results"]) == 1
    assert result["results"][0]["path"] == "notes.md"


def test_search_no_matches(tmp):
    (tmp / ".sal").mkdir()
    db = core._open_db()
    db.execute("INSERT INTO docs(path, body) VALUES (?, ?)",
               ("notes.md", "nothing relevant here"))
    db.commit(); db.close()
    result = json.loads(Search("local volatility"))
    assert result["results"] == []


def test_search_invalid_query(tmp):
    (tmp / ".sal").mkdir()
    core._open_db().close()
    result = json.loads(Search("*"))
    assert "error" in result


# ── List topic filtering ─────────────────────────────────────────────────────

def test_list_topic_filters_match():
    cards = [
        {"title": "A", "path": "a.pdf", "topics": ["local volatility", "stochastic"]},
        {"title": "B", "path": "b.pdf", "topics": ["credit risk"]},
    ]
    with patch.object(core, "CARDS", cards):
        result = json.loads(List(topic="volatility"))
    assert len(result["documents"]) == 1
    assert result["documents"][0]["title"] == "A"


def test_list_topic_filters_no_match():
    cards = [
        {"title": "A", "path": "a.pdf", "topics": ["stochastic calculus"]},
    ]
    with patch.object(core, "CARDS", cards):
        result = json.loads(List(topic="credit"))
    assert result["documents"] == []


def test_list_topic_filters_case_insensitive():
    cards = [
        {"title": "A", "path": "a.pdf", "topics": ["Black-Scholes"]},
    ]
    with patch.object(core, "CARDS", cards):
        result = json.loads(List(topic="black-scholes"))
    assert len(result["documents"]) == 1


def test_list_topic_none_returns_all():
    cards = [
        {"title": "A", "path": "a.pdf", "topics": ["vol"]},
        {"title": "B", "path": "b.pdf", "topics": ["credit"]},
    ]
    with patch.object(core, "CARDS", cards):
        result = json.loads(List(topic=None))
    assert len(result["documents"]) == 2
