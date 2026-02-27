"""sal MCP server — exposes document tools for a knowledge-base directory."""
import anthropic
import fitz
import json, os, sqlite3, sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP

MODEL       = "claude-opus-4-6"
EXTS        = (".pdf", ".md", ".txt", ".html")
CARD_PROMPT = (
    'Extract metadata from this document for an AI tutor. Return JSON only — no prose, no fences:\n'
    '{"title": "...", "type": "paper|chapter|notes|article|other", "topics": ["3-6 key concepts"],\n'
    ' "summary": "1-2 sentences", "sections": [{"loc": "page or heading", "desc": "what it covers"}]}\n'
    'For PDFs use page numbers (e.g. "p3-5"). For markdown use heading names.\n'
)

# Module-level state, populated at startup before mcp.run()
CARDS: list[dict] = []
WS: Path = Path.cwd()

mcp = FastMCP("sal")


def _read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n\n".join(p.get_text() for p in fitz.open(str(path)))
    return path.read_text(errors="replace")


def _resources() -> list[Path]:
    return sorted(f for ext in EXTS for f in WS.glob(f"*{ext}")) if WS.exists() else []


def _open_db() -> sqlite3.Connection:
    db = sqlite3.connect(WS / ".sal" / "search.db")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(path UNINDEXED, body)")
    db.commit()
    return db


# ── Indexing ──────────────────────────────────────────────────────────────────

def _index_one(f: Path, client: anthropic.Anthropic) -> dict:
    content = _read_file(f)[:15000]
    r = client.messages.create(model=MODEL, max_tokens=1024, system=CARD_PROMPT,
        messages=[{"role": "user", "content": f"path: {f.name}\n\n{content}"}])
    text = r.content[0].text.strip()
    if text.startswith("```"):
        _, _, text = text.partition("\n")
        text = text.rstrip("`").strip()
    card = json.loads(text)
    card["path"] = str(f.relative_to(WS))
    return card


def ensure_indexed(client: anthropic.Anthropic) -> list[dict]:
    (WS / ".sal" / "index").mkdir(parents=True, exist_ok=True)
    db = _open_db()
    cards = []
    for f in _resources():
        cp = WS / ".sal" / "index" / (f.name + ".json")
        if not cp.exists():
            print(f"  · indexing {f.name}…", end="", flush=True)
            card = _index_one(f, client)
            cp.write_text(json.dumps(card, indent=2))
            print(" done")
        else:
            card = json.loads(cp.read_text())
        cards.append(card)

        path_key = str(f.relative_to(WS))
        if not db.execute("SELECT 1 FROM docs WHERE path=?", (path_key,)).fetchone():
            db.execute("INSERT INTO docs(path, body) VALUES (?,?)",
                       (path_key, _read_file(f)))
    db.commit()
    db.close()
    return cards


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def List() -> str:
    """List all indexed documents with title, summary, topics, and section map."""
    return json.dumps({"documents": CARDS})


@mcp.tool()
def Read(path: str, page: int | None = None) -> str:
    """Read a document from the knowledge base. Use page (0-indexed) for a specific PDF page."""
    f = WS / path
    if not f.exists():
        return json.dumps({"error": f"Not found: {path}"})
    if page is not None and f.suffix.lower() == ".pdf":
        doc = fitz.open(str(f))
        if not 0 <= page < len(doc):
            return json.dumps({"error": f"Page {page} out of range"})
        content = doc[page].get_text()
    else:
        db = _open_db()
        row = db.execute("SELECT body FROM docs WHERE path=?",
                         (str(f.relative_to(WS)),)).fetchone()
        db.close()
        content = row[0] if row else _read_file(f)
    if len(content) > 8000:
        content = content[:8000] + "\n…[truncated — specify page for more]"
    return json.dumps({"path": path, "content": content})


@mcp.tool()
def Search(query: str, max_results: int = 5) -> str:
    """Full-text search across all documents. Returns BM25-ranked results with context snippets."""
    db = _open_db()
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


# ── Startup ───────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return key
    sys.exit("Error: set ANTHROPIC_API_KEY")


def _cmd_init():
    global WS
    WS = Path.cwd()
    client = anthropic.Anthropic(api_key=get_api_key(), max_retries=5)
    cards = ensure_indexed(client)
    print(f"\n{len(cards)} document(s) indexed in {WS}")


def _cmd_ls():
    index_dir = Path.cwd() / ".sal" / "index"
    if not index_dir.exists():
        sys.exit("Not indexed. Run: sal init")
    cards = [json.loads(p.read_text()) for p in sorted(index_dir.glob("*.json"))]
    if not cards:
        sys.exit("No documents indexed. Run: sal init")
    print(f"\n{len(cards)} document(s) in {Path.cwd()}\n")
    for c in cards:
        print(f"  {c.get('title', c.get('path', '?'))}  ({c.get('type', '?')})")
        print(f"  path: {c.get('path', '?')}")
        print(f"  topics: {', '.join(c.get('topics', []))}")
        if c.get("summary"):
            print(f"  {c['summary']}")
        print()


def main():
    import argparse
    global WS, CARDS
    parser = argparse.ArgumentParser(prog="sal")
    parser.add_argument("--resources", help="Resources directory (MCP server mode)")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init", help="Index documents in current directory")
    subparsers.add_parser("ls", help="List indexed documents in current directory")
    args = parser.parse_args()

    if args.command == "init":
        _cmd_init()
    elif args.command == "ls":
        _cmd_ls()
    else:
        if not args.resources:
            parser.print_help()
            sys.exit(1)
        WS = Path(args.resources).resolve()
        client = anthropic.Anthropic(api_key=get_api_key(), max_retries=5)
        CARDS = ensure_indexed(client)
        mcp.run()


if __name__ == "__main__":
    main()
