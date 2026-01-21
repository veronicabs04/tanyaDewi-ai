# my_agent/retrieval_tool.py
import re
import sqlite3
import json
from typing import List, Dict, Any
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "knowledge.db"

def _build_fallback_query(query: str) -> str | None:
    terms = [t for t in re.findall(r"[\w]+", query.lower()) if len(t) >= 3]
    if not terms:
        return None
    # Use OR so multi-word questions still return partial matches.
    return " OR ".join(f"{t}*" for t in terms)


def search_report(query: str, k: int = 5) -> dict:
    print(f"[TOOL] search_report called: query={query!r}, k={k}, db={DB_PATH}")
    if not DB_PATH.exists():
        return {"query": query, "results": [], "error": f"DB not found: {DB_PATH}"}

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    def run(q: str):
        # pakai bm25 kalau FTS5
        try:
            cur.execute(
                "SELECT chunk, source, page FROM report_fts WHERE report_fts MATCH ? ORDER BY bm25(report_fts) LIMIT ?",
                (q, k),
            )
            return cur.fetchall()
        except sqlite3.OperationalError:
            # fallback tanpa bm25 (misal FTS4)
            cur.execute(
                "SELECT chunk, source, page FROM report_fts WHERE report_fts MATCH ? LIMIT ?",
                (q, k),
            )
            return cur.fetchall()

    rows = []
    try:
        rows = run(query)
    except sqlite3.OperationalError:
        rows = []

    if not rows:
        fallback_query = _build_fallback_query(query)
        if fallback_query:
            try:
                rows = run(fallback_query)
            except sqlite3.OperationalError:
                rows = []

    conn.close()
    results = [{"text": r[0], "source": r[1], "page": r[2]} for r in rows]
    return {"query": query, "results": results}
