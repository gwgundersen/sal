#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "anthropic",
#   "pymupdf",
#   "pylatexenc",
# ]
# ///
"""sal.py — Sal, minimal AI tutor (single-file MVP)

Usage:
    cd ~/my-learning-dir
    uv run /path/to/sal.py
"""
import json
import os
import shutil
import sys
from pathlib import Path

import anthropic

try:
    import fitz
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from pylatexenc.latex2text import LatexNodes2Text
    _l2t = LatexNodes2Text()
    HAS_LATEX = True
except ImportError:
    HAS_LATEX = False

MODEL = "claude-opus-4-6"
RESOURCE_EXTS = (".pdf", ".md", ".txt", ".html", ".py")


# ── Helpers ───────────────────────────────────────────────────────────────────

def ws() -> Path:
    return Path.cwd()

def _index_dir() -> Path:
    return ws() / ".sal" / "index"

def _read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        if HAS_PDF:
            doc = fitz.open(str(path))
            return "\n\n".join(page.get_text() for page in doc)
        return "[PDF support unavailable — install PyMuPDF: pip install pymupdf]"
    return path.read_text(errors="replace")


# ── Math rendering ────────────────────────────────────────────────────────────

class MathRenderer:
    """Convert LaTeX math to unicode on-the-fly during streaming.

    Prose is passed through immediately. When a $ or $$ delimiter is seen,
    content is buffered until the closing delimiter, then converted and flushed.
    Handles chunk boundaries safely via a one-char lookahead buffer.
    """

    def __init__(self):
        self._pending = ""   # at most one "$" carried across chunk boundary
        self._math = ""      # accumulated math content
        self._in_math = False
        self._display = False

    def feed(self, chunk: str) -> str:
        out = []
        text = self._pending + chunk
        self._pending = ""
        i = 0
        while i < len(text):
            ch = text[i]
            if not self._in_math:
                if ch == "$":
                    if i + 1 >= len(text):
                        self._pending = "$"   # wait for next chunk
                        break
                    if text[i + 1] == "$":
                        self._in_math = True
                        self._display = True
                        self._math = ""
                        i += 2
                    else:
                        self._in_math = True
                        self._display = False
                        self._math = ""
                        i += 1
                else:
                    out.append(ch)
                    i += 1
            else:
                if ch == "$":
                    if self._display:
                        if i + 1 >= len(text):
                            self._pending = "$"
                            break
                        if text[i + 1] == "$":
                            out.append(self._convert(self._math, display=True))
                            self._in_math = False
                            i += 2
                        else:
                            self._math += ch
                            i += 1
                    else:
                        out.append(self._convert(self._math, display=False))
                        self._in_math = False
                        i += 1
                else:
                    self._math += ch
                    i += 1
        return "".join(out)

    def flush(self) -> str:
        """Emit any buffered content at end of response."""
        if self._in_math:
            delim = "$$" if self._display else "$"
            result = delim + self._math + self._pending + delim
        else:
            result = self._pending
        self._pending = self._math = ""
        self._in_math = self._display = False
        return result

    def _convert(self, latex: str, display: bool) -> str:
        if HAS_LATEX:
            try:
                text = _l2t.latex_to_text(latex).strip()
            except Exception:
                text = latex
        else:
            text = latex
        return f"\n\n  {text}\n\n" if display else text


# ── Indexing ──────────────────────────────────────────────────────────────────

CARD_PROMPT = """\
Extract structured metadata from this document to help an AI tutor understand its contents.

Return a JSON object with exactly these fields:
{
  "title": "full document title",
  "type": "research_paper | textbook_chapter | lecture_notes | tutorial | article | other",
  "topics": ["3-8 key concepts covered"],
  "summary": "2-3 sentences: what this document is about and its key contribution or takeaway",
  "sections": [
    {"loc": "page or heading reference", "desc": "what this section covers"}
  ]
}

For PDFs use page numbers for loc (e.g. "p3-5"). For markdown use heading names.
Return only valid JSON, no prose, no code fences."""


def _card_path(resource: Path) -> Path:
    return _index_dir() / (resource.name + ".json")


def _is_stale(resource: Path, card: Path) -> bool:
    if not card.exists():
        return True
    try:
        cached = json.loads(card.read_text())
        return resource.stat().st_mtime > cached.get("_mtime", 0)
    except Exception:
        return True


def _index_one(resource: Path, client: anthropic.Anthropic) -> dict:
    content = _read_file(resource)
    if len(content) > 15000:
        content = content[:15000] + "\n…[truncated]"

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=CARD_PROMPT,
        messages=[{"role": "user", "content": f"Document path: {resource.name}\n\n{content}"}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    card = json.loads(text)
    card["path"] = str(resource.relative_to(ws()))
    card["_mtime"] = resource.stat().st_mtime
    return card


def ensure_indexed(client: anthropic.Anthropic) -> list[dict]:
    """Index any new or changed resources. Returns all cards."""
    resources_dir = ws() / "resources"
    if not resources_dir.exists():
        return []

    _index_dir().mkdir(parents=True, exist_ok=True)

    files = sorted(
        f for ext in RESOURCE_EXTS for f in resources_dir.rglob(f"*{ext}")
    )

    cards = []
    for f in files:
        card_path = _card_path(f)
        if _is_stale(f, card_path):
            print(f"  · indexing {f.name} …", end="", flush=True)
            try:
                card = _index_one(f, client)
                card_path.write_text(json.dumps(card, indent=2))
                print(" done")
            except Exception as e:
                print(f" failed ({e})")
                continue
        else:
            card = json.loads(card_path.read_text())
        cards.append(card)

    return cards


# ── Tool implementations ──────────────────────────────────────────────────────

def list_documents() -> dict:
    resources_dir = ws() / "resources"
    if not resources_dir.exists():
        return {"documents": []}
    docs = sorted(
        str(f.relative_to(ws()))
        for ext in RESOURCE_EXTS
        for f in resources_dir.rglob(f"*{ext}")
    )
    return {"documents": docs}


def read_document(path: str, page: int = None) -> dict:
    full = ws() / path
    if not full.exists():
        return {"error": f"Not found: {path}"}
    if page is not None and full.suffix.lower() == ".pdf" and HAS_PDF:
        doc = fitz.open(str(full))
        if not (0 <= page < len(doc)):
            return {"error": f"Page {page} out of range (doc has {len(doc)} pages)"}
        content = doc[page].get_text()
    else:
        content = _read_file(full)
    if len(content) > 8000:
        content = content[:8000] + "\n…[truncated — use page parameter to read more]"
    return {"path": path, "content": content}


def search(query: str, max_results: int = 5) -> dict:
    q = query.lower()
    results, count = [], 0
    search_dirs = [ws() / "resources", ws() / "notes"]
    for d in search_dirs:
        if not d.exists() or count >= max_results:
            continue
        for ext in RESOURCE_EXTS:
            for f in d.rglob(f"*{ext}"):
                if count >= max_results:
                    break
                try:
                    text = _read_file(f)
                except Exception:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if q in line.lower():
                        results.append({
                            "path": str(f.relative_to(ws())),
                            "line": i,
                            "text": line.strip(),
                        })
                        count += 1
                        if count >= max_results:
                            break
    return {"query": query, "results": results}


def write_note(path: str, content: str) -> dict:
    notes_dir = ws() / "notes"
    notes_dir.mkdir(exist_ok=True)
    (notes_dir / path).write_text(content)
    return {"success": True, "path": f"notes/{path}"}


def read_note(path: str) -> dict:
    p = ws() / "notes" / path
    if not p.exists():
        return {"error": f"Note not found: {path}"}
    return {"content": p.read_text()}


def list_notes() -> dict:
    notes_dir = ws() / "notes"
    if not notes_dir.exists():
        return {"notes": []}
    return {"notes": [f.name for f in sorted(notes_dir.glob("*.md"))]}


DISPATCH = {
    "list_documents": list_documents,
    "read_document": read_document,
    "search": search,
    "write_note": write_note,
    "read_note": read_note,
    "list_notes": list_notes,
}


def run_tool(name: str, inputs: dict) -> str:
    fn = DISPATCH.get(name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {name}"})
    return json.dumps(fn(**inputs))


# ── Tool schemas ──────────────────────────────────────────────────────────────

TOOLS = [
    {"name": "list_documents",
     "description": "List all documents in resources/.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},

    {"name": "read_document",
     "description": "Read a document. For long PDFs specify a page (0-indexed).",
     "input_schema": {"type": "object", "required": ["path"], "properties": {
         "path": {"type": "string", "description": "Relative path, e.g. resources/paper.pdf"},
         "page": {"type": "integer", "description": "PDF page number (0-indexed)"}}}},

    {"name": "search",
     "description": "Keyword search across resources and notes.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"},
         "max_results": {"type": "integer", "default": 5}}}},

    {"name": "write_note",
     "description": "Create or update a learner note in notes/ (markdown).",
     "input_schema": {"type": "object", "required": ["path", "content"], "properties": {
         "path": {"type": "string", "description": "Filename, e.g. transformers.md"},
         "content": {"type": "string"}}}},

    {"name": "read_note",
     "description": "Read an existing learner note.",
     "input_schema": {"type": "object", "required": ["path"], "properties": {
         "path": {"type": "string"}}}},

    {"name": "list_notes",
     "description": "List all learner notes.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
]


DEFAULT_SAL_MD = """\
# Sal — Tutor Personality

## Style
- Always write math in LaTeX: $...$ for inline, $$...$$ for display. Never use plain-text math.
- Be concise. Short, direct responses over long ones.
- Socratic: ask one question at a time. Don't pile on options.
- Don't narrate your tool use or explain what you're about to do — just do it, then respond.
- Avoid filler ("Great question!", "Certainly!", "Let me..."). Get to the point.
- Use the learner's own words when possible to build on what they already know.

## Pedagogy
- Ask what the learner already knows before explaining.
- Use a concrete example or analogy before any formal definition.
- After explaining something, ask one question to check understanding.
- Suggest note-taking only when a concept is genuinely worth capturing.
"""


# ── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(cards: list[dict]) -> str:
    root = ws()
    lines = [
        f"You are Sal, an AI tutor. The learner's workspace is at: {root}",
        "",
        "Core rules:",
        "- Use tools silently. Never narrate between tool calls ('let me check...', 'that was front matter...'). Gather everything you need, then give one focused response.",
        "- Be concise by default. The learner can always ask for more.",
        "- Socratic: guide discovery with questions rather than lectures.",
        "- Ground explanations in the actual documents — use read_document and search.",
        "- Prefer concrete examples before formal definitions.",
        "- Always write math in LaTeX: $...$ for inline, $$...$$ for display equations. Never use plain-text math notation.",
        "",
    ]

    if cards:
        lines.append("## Available resources\n")
        for card in cards:
            lines.append(f"### {card.get('title', card['path'])}")
            lines.append(f"path: {card['path']}  |  type: {card.get('type', '?')}")
            if card.get("topics"):
                lines.append(f"topics: {', '.join(card['topics'])}")
            if card.get("summary"):
                lines.append(card["summary"])
            if card.get("sections"):
                lines.append("sections: " + " · ".join(
                    f"{s['loc']}: {s['desc']}" for s in card["sections"]
                ))
            lines.append("")

    sal_md = root / "SAL.md"
    if sal_md.exists():
        lines += ["## Tutor instructions (SAL.md)", sal_md.read_text(), ""]
    learner_md = root / "LEARNER.md"
    if learner_md.exists():
        lines += ["## Learner profile (LEARNER.md)", learner_md.read_text(), ""]

    return "\n".join(lines)


# ── Main loop ─────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    config_path = ws() / ".sal" / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            key = cfg.get("api_key", "")
            if key:
                return key
        except Exception:
            pass
    print("Error: no API key found.")
    print("Set ANTHROPIC_API_KEY in your environment, or add it to .sal/config.json:")
    print('  {"api_key": "sk-ant-..."}')
    sys.exit(1)


def _format_tool(name: str, inputs: dict) -> str:
    if name == "read_document":
        filename = Path(inputs.get("path", "")).name
        page = inputs.get("page")
        return f"Read({filename}{f' p{page}' if page is not None else ''})"
    if name == "search":
        return f"Search({inputs.get('query', '')!r})"
    if name == "write_note":
        return f"Write({inputs.get('path', '')})"
    if name == "read_note":
        return f"Read(notes/{inputs.get('path', '')})"
    if name == "list_documents":
        return "List(resources)"
    if name == "list_notes":
        return "List(notes)"
    return name


def _w() -> int:
    return shutil.get_terminal_size((80, 24)).columns


def _prompt() -> str:
    w = _w()
    bar = "─" * (w - 2)
    print(f"╭{bar}╮")
    try:
        text = input("│ > ")
    except (EOFError, KeyboardInterrupt):
        print(f"╰{bar}╯")
        raise
    print(f"╰{bar}╯")
    return text.strip()


def main():
    client = anthropic.Anthropic(api_key=get_api_key())

    print(f"\n  ◆ sal  ·  {ws()}\n")

    sal_md = ws() / "SAL.md"
    if not sal_md.exists():
        sal_md.write_text(DEFAULT_SAL_MD)

    cards = ensure_indexed(client)
    if cards:
        print(f"  {len(cards)} resource{'s' if len(cards) != 1 else ''} indexed\n")

    system = build_system_prompt(cards)
    messages = []

    while True:
        try:
            user_input = _prompt()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            break

        messages.append({"role": "user", "content": user_input})

        while True:
            renderer = MathRenderer()
            with client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=system,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                first_text = True
                current_block_type = None
                for event in stream:
                    if event.type == "content_block_start":
                        current_block_type = event.content_block.type
                        if current_block_type == "text" and first_text:
                            print("\n  ◆ ", end="", flush=True)
                            first_text = False
                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            print(renderer.feed(event.delta.text), end="", flush=True)
                print(renderer.flush(), end="", flush=True)
                response = stream.get_final_message()

            print("\n")

            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "end_turn" or not tool_uses:
                break

            tool_results = []
            for tu in tool_uses:
                print(f"  · {_format_tool(tu.name, tu.input)}", flush=True)
                result = run_tool(tu.name, tu.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

        print()


if __name__ == "__main__":
    main()
