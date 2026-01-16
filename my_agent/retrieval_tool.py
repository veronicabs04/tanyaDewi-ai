# my_agent/retrieval_tool.py
import re
import sqlite3
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
        return {
            "query": query,
            "results": [],
            "error": f"DB not found: {DB_PATH}",
        }
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT chunk, source, page FROM report_fts WHERE report_fts MATCH ? LIMIT ?",
        (query, k),
    )
    rows = cur.fetchall()
    if not rows:
        fallback_query = _build_fallback_query(query)
        if fallback_query:
            cur.execute(
                "SELECT chunk, source, page FROM report_fts WHERE report_fts MATCH ? LIMIT ?",
                (fallback_query, k),
            )
            rows = cur.fetchall()
    conn.close()
    results = [{"text": r[0], "source": r[1], "page": r[2]} for r in rows]
    return {"query": query, "results": results}
