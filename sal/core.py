"""Shared state and logic for sal."""
import anthropic
import fitz
import json, os, sqlite3, sys
from pathlib import Path

MODEL       = "claude-opus-4-6"
EXTS        = (".pdf", ".md", ".txt", ".html", ".org")
CARD_PROMPT = (
    'Extract metadata from this document for an AI tutor. Return JSON only — no prose, no fences:\n'
    '{"title": "...", "type": "paper|chapter|notes|article|other", "topics": ["3-6 key concepts"],\n'
    ' "summary": "1-2 sentences", "sections": [{"loc": "page or heading", "desc": "what it covers"}],\n'
    ' "key_terms": ["important terms/definitions introduced"],\n'
    ' "prerequisites": ["concepts the reader should already know"],\n'
    ' "key_results": ["main theorems, equations, or conclusions"]}\n'
    'For PDFs use page numbers (e.g. "p3-5"). For markdown use heading names.\n'
)

# Module-level state, populated at startup
CARDS: list[dict] = []
WS: Path = Path.cwd()


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


def _index_one(f: Path, client: anthropic.Anthropic) -> dict:
    content = _read_file(f)[:15000]
    r = client.messages.create(model=MODEL, max_tokens=2048, system=CARD_PROMPT,
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


def get_api_key() -> str:
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return key
    sys.exit("Error: set ANTHROPIC_API_KEY")
