# my_agent/retrieval_tool.py
import sqlite3

DB_PATH = "data/knowledge.db"

def search_report(query: str, k: int = 5) -> dict:
    """
    Tool: cari potongan teks dari PDF yang sudah di-ingest ke SQLite FTS.
    Return dict supaya enak dibaca agent.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT chunk, source, page FROM report_fts WHERE report_fts MATCH ? LIMIT ?",
        (query, k),
    )
    rows = cur.fetchall()
    conn.close()
    results = [{"text": r[0], "source": r[1], "page": r[2]} for r in rows]
    return {"query": query, "results": results}
