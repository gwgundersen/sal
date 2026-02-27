"""Unit tests for sal.py — Anthropic API calls are mocked throughout."""
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
import sal


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp(tmp_path):
    """Patch sal.WS to a fresh temp directory."""
    with patch("sal.WS", tmp_path):
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
    assert sal._read_file(f) == "hello world"


def test_read_pdf_via_fitz():
    page = MagicMock()
    page.get_text.return_value = "page content"
    with patch("sal.fitz.open", return_value=[page]):
        result = sal._read_file(Path("test.pdf"))
    assert result == "page content"


# ── _resources ────────────────────────────────────────────────────────────────

def test_resources_lists_supported_extensions(tmp):
    (tmp / "doc.pdf").touch()
    (tmp / "notes.md").touch()
    (tmp / "ignore.xyz").touch()
    names = [f.name for f in sal._resources()]
    assert "doc.pdf" in names
    assert "notes.md" in names
    assert "ignore.xyz" not in names


def test_resources_empty_when_no_matching_files(tmp):
    (tmp / "ignore.xyz").touch()
    assert sal._resources() == []


# ── _index_one ────────────────────────────────────────────────────────────────

def test_index_one_parses_json(tmp):
    card = {"title": "Test Doc", "type": "paper", "topics": ["vol"], "summary": "A test."}
    f = tmp / "doc.txt"
    f.write_text("some content")
    result = sal._index_one(f, mock_client(card))
    assert result["title"] == "Test Doc"
    assert "path" in result


def test_index_one_strips_code_fences(tmp):
    card = {"title": "Fenced", "type": "paper", "topics": [], "summary": ""}
    fenced = f"```json\n{json.dumps(card)}\n```"
    response = MagicMock()
    response.content = [MagicMock(text=fenced)]
    client = MagicMock()
    client.messages.create.return_value = response
    f = tmp / "doc.txt"
    f.write_text("content")
    result = sal._index_one(f, client)
    assert result["title"] == "Fenced"


# ── Tools ─────────────────────────────────────────────────────────────────────

def test_list_returns_cards():
    cards = [{"title": "My Paper", "path": "paper.pdf", "type": "paper",
              "topics": ["vol"], "summary": "About vol.", "sections": []}]
    with patch("sal.CARDS", cards):
        result = json.loads(sal.List())
    assert result["documents"] == cards


def test_read_not_found(tmp):
    result = json.loads(sal.Read("missing.txt"))
    assert "error" in result


def test_read_returns_content(tmp):
    (tmp / ".sal").mkdir()
    db = sal._open_db()
    db.execute("INSERT INTO docs(path, body) VALUES (?, ?)", ("doc.txt", "hello"))
    db.commit(); db.close()
    (tmp / "doc.txt").write_text("hello")
    result = json.loads(sal.Read("doc.txt"))
    assert result["content"] == "hello"


def test_search_finds_match(tmp):
    (tmp / ".sal").mkdir()
    db = sal._open_db()
    db.execute("INSERT INTO docs(path, body) VALUES (?, ?)",
               ("notes.md", "local volatility is important\nother stuff"))
    db.commit(); db.close()
    result = json.loads(sal.Search("local volatility"))
    assert len(result["results"]) == 1
    assert result["results"][0]["path"] == "notes.md"


def test_search_no_matches(tmp):
    (tmp / ".sal").mkdir()
    db = sal._open_db()
    db.execute("INSERT INTO docs(path, body) VALUES (?, ?)",
               ("notes.md", "nothing relevant here"))
    db.commit(); db.close()
    result = json.loads(sal.Search("local volatility"))
    assert result["results"] == []


def test_search_invalid_query(tmp):
    (tmp / ".sal").mkdir()
    sal._open_db().close()
    result = json.loads(sal.Search("*"))
    assert "error" in result
