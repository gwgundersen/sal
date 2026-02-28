"""MCP server and tool definitions for sal."""
import json
import sqlite3
import fitz
from mcp.server.fastmcp import FastMCP
import sal.core as core

mcp = FastMCP("sal")


@mcp.tool()
def List(topic: str | None = None) -> str:
    """List all indexed documents with title, summary, topics, and section map."""
    cards = core.CARDS
    if topic:
        topic_lower = topic.lower()
        cards = [c for c in cards if any(topic_lower in t.lower() for t in c.get("topics", []))]
    return json.dumps({"documents": cards})


@mcp.tool()
def Read(path: str, page: int | None = None) -> str:
    """Read a document from the knowledge base. Use page (0-indexed) for a specific PDF page."""
    f = core.WS / path
    if not f.exists():
        return json.dumps({"error": f"Not found: {path}"})
    if page is not None and f.suffix.lower() == ".pdf":
        doc = fitz.open(str(f))
        if not 0 <= page < len(doc):
            return json.dumps({"error": f"Page {page} out of range"})
        content = doc[page].get_text()
    else:
        db = core._open_db()
        row = db.execute("SELECT body FROM docs WHERE path=?",
                         (str(f.relative_to(core.WS)),)).fetchone()
        db.close()
        content = row[0] if row else core._read_file(f)
    if len(content) > 8000:
        content = content[:8000] + "\n…[truncated — specify page for more]"
    return json.dumps({"path": path, "content": content})


@mcp.tool()
def Search(query: str, max_results: int = 5) -> str:
    """Full-text search across all documents. Returns BM25-ranked results with context snippets."""
    db = core._open_db()
    try:
        rows = db.execute("""
            SELECT path, snippet(docs, 1, '«', '»', '…', 24)
            FROM docs WHERE docs MATCH ?
            ORDER BY rank LIMIT ?
        """, (query, max_results)).fetchall()
    except sqlite3.OperationalError:
        return json.dumps({"query": query, "results": [], "error": "invalid query syntax"})
    db.close()
    return json.dumps({"query": query,
                       "results": [{"path": p, "snippet": s} for p, s in rows]})
