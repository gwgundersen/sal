"""Flask web UI for browsing the sal knowledge base."""
import json
import sqlite3
from pathlib import Path
from flask import Flask, request, render_template_string
from sal.core import CARDS, WS, _open_db

app = Flask(__name__)

BASE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}sal{% endblock %}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; line-height: 1.6;
         max-width: 860px; margin: 0 auto; padding: 2rem 1rem; color: #1a1a1a; }
  a { color: #2563eb; text-decoration: none; }
  a:hover { text-decoration: underline; }
  h1 { font-size: 1.5rem; margin-bottom: 1rem; }
  h2 { font-size: 1.2rem; margin-bottom: 0.5rem; }
  .nav { display: flex; gap: 1.5rem; align-items: center; margin-bottom: 2rem;
         padding-bottom: 1rem; border-bottom: 1px solid #e5e5e5; }
  .nav .brand { font-weight: 700; font-size: 1.2rem; color: #1a1a1a; }
  .search-form { display: flex; gap: 0.5rem; }
  .search-form input[type=text] { padding: 0.4rem 0.8rem; border: 1px solid #d1d5db;
    border-radius: 6px; font-size: 0.95rem; width: 220px; }
  .search-form button { padding: 0.4rem 1rem; background: #2563eb; color: #fff;
    border: none; border-radius: 6px; cursor: pointer; font-size: 0.95rem; }
  .search-form button:hover { background: #1d4ed8; }
  .card { border: 1px solid #e5e5e5; border-radius: 8px; padding: 1rem;
          margin-bottom: 1rem; }
  .card h2 a { color: #1a1a1a; }
  .card .meta { font-size: 0.85rem; color: #6b7280; margin-top: 0.25rem; }
  .card .summary { margin-top: 0.5rem; }
  .topics { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }
  .topics span { background: #eff6ff; color: #2563eb; padding: 0.15rem 0.6rem;
    border-radius: 999px; font-size: 0.8rem; }
  .content { white-space: pre-wrap; font-family: 'SF Mono', Menlo, monospace;
    font-size: 0.9rem; background: #f9fafb; padding: 1rem; border-radius: 8px;
    border: 1px solid #e5e5e5; overflow-x: auto; }
  .snippet { font-size: 0.9rem; color: #374151; margin-top: 0.25rem; }
  .back { margin-bottom: 1rem; display: inline-block; }
</style>
</head>
<body>
<nav class="nav">
  <a class="brand" href="/">sal</a>
  <form class="search-form" action="/search" method="get">
    <input type="text" name="q" placeholder="Search documents…" value="{{ query|default('') }}">
    <button type="submit">Search</button>
  </form>
</nav>
{% block body %}{% endblock %}
</body>
</html>"""

INDEX_PAGE = BASE.replace("{% block title %}sal{% endblock %}", "{% block title %}sal — documents{% endblock %}").replace(
    "{% block body %}{% endblock %}",
    """{% block body %}
<h1>{{ cards|length }} document{{ 's' if cards|length != 1 else '' }}</h1>
{% for c in cards %}
<div class="card">
  <h2><a href="/card/{{ c.path }}">{{ c.title or c.path }}</a></h2>
  {% if c.summary %}<div class="summary">{{ c.summary }}</div>{% endif %}
  {% if c.topics %}
  <div class="topics">{% for t in c.topics %}<span>{{ t }}</span>{% endfor %}</div>
  {% endif %}
</div>
{% endfor %}
{% endblock %}""")

CARD_PAGE = BASE.replace("{% block title %}sal{% endblock %}", "{% block title %}{{ card.title or card.path }} — sal{% endblock %}").replace(
    "{% block body %}{% endblock %}",
    """{% block body %}
<a class="back" href="/">&larr; All documents</a>
<h1>{{ card.title or card.path }}</h1>
<div class="meta" style="margin-bottom:1rem;">Edit this card: <code>{{ card_file }}</code></div>
<div class="content">{{ card_json }}</div>
{% endblock %}""")

SEARCH_PAGE = BASE.replace("{% block title %}sal{% endblock %}", "{% block title %}search: {{ query }} — sal{% endblock %}").replace(
    "{% block body %}{% endblock %}",
    """{% block body %}
<h1>{{ results|length }} result{{ 's' if results|length != 1 else '' }} for "{{ query }}"</h1>
{% if error %}<p style="color:#dc2626;">{{ error }}</p>{% endif %}
{% for r in results %}
<div class="card">
  <h2><a href="/read/{{ r.path }}">{{ r.path }}</a></h2>
  <div class="snippet">{{ r.snippet }}</div>
</div>
{% endfor %}
{% if not results and not error %}<p>No results found.</p>{% endif %}
{% endblock %}""")


@app.route("/")
def index():
    return render_template_string(INDEX_PAGE, cards=CARDS)


@app.route("/card/<path:path>")
def card(path):
    for c in CARDS:
        if c.get("path") == path:
            card_file = str(WS / ".sal" / "index" / (Path(path).name + ".json"))
            return render_template_string(CARD_PAGE, card=c,
                                          card_file=card_file,
                                          card_json=json.dumps(c, indent=2))
    return render_template_string(BASE.replace("{% block body %}{% endblock %}",
        "{% block body %}<p>No card found for: {{ path }}</p>{% endblock %}"), path=path), 404


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return render_template_string(SEARCH_PAGE, query="", results=[], error=None)
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
    return render_template_string(SEARCH_PAGE, query=q, results=results, error=error)
