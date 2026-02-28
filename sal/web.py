"""Flask web UI for browsing the sal knowledge base."""
import json
import sqlite3
from pathlib import Path
from flask import Flask, request, render_template
from sal.core import CARDS, WS, _open_db

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", cards=CARDS)


@app.route("/card/<path:path>")
def card(path):
    for c in CARDS:
        if c.get("path") == path:
            card_file = str(WS / ".sal" / "index" / (Path(path).name + ".json"))
            return render_template("card.html", card=c,
                                   card_file=card_file,
                                   card_json=json.dumps(c, indent=2))
    return render_template("error.html", path=path), 404


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return render_template("search.html", query="", results=[], error=None)
    db = _open_db()
    error = None
    try:
        rows = db.execute("""
            SELECT path, snippet(docs, 1, '<b>', '</b>', '…', 24)
            FROM docs WHERE docs MATCH ?
            ORDER BY rank LIMIT 20
        """, (q,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
        error = "Invalid search query syntax."
    db.close()
    results = [{"path": p, "snippet": s} for p, s in rows]
    return render_template("search.html", query=q, results=results, error=error)
