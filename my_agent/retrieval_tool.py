# my_agent/retrieval_tool.py
import re
import sqlite3
from typing import List
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "knowledge.db"

STOPWORDS = {
    "yang", "untuk", "dengan", "pada", "dan", "adalah", "atau", "dari",
    "ini", "itu", "sebagai", "di", "ke", "oleh", "juga", "karena",
    "tetapi", "saat", "jika", "akan", "lebih", "bisa", "sudah",
    "masih", "jadi", "apa", "saya", "kamu", "dia", "mereka", "resep", "cara", "buat", "bikin", "membuat",
    "gimana", "bagaimana", "apa",
    "tolong", "dong", "ya",
    "bisa", "saya", "aku", "kamu"
}

def _build_fallback_query(query: str) -> str | None:
    terms = [t for t in re.findall(r"[\w]+", query.lower()) if len(t) >= 3]
    if not terms:
        return None
    # Use OR so multi-word questions still return partial matches.
    return " OR ".join(f"{t}*" for t in terms)

def _clean_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", query.lower())
    tokens = [t for t in tokens if t not in STOPWORDS]
    return " ".join(tokens)

def _expand_queries(query: str) -> List[str]:
    q = query.strip()
    if not q:
        return [query]

    ql = q.lower()
    ql = re.sub(r"\s+", " ", ql).strip()

    variants = [ql]

    # Singkatan umum UMKM / medsos
    repl = {
        r"\bfb\b": "facebook",
        r"\big\b": "instagram",
        r"\bwa\b": "whatsapp",
        r"\btokped\b": "tokopedia",
        r"\bshopeefood\b": "shopee food",
        # keep tiktok as-is, but some docs might have "tik tok"
    }

    q2 = ql
    for pat, rep in repl.items():
        q2 = re.sub(pat, rep, q2)
    if q2 != ql:
        variants.append(q2)

    # Variasi kata yang sering beda di dokumen vs pertanyaan user
    variants.append(re.sub(r"\bjualan\b", "berjualan", q2))
    variants.append(re.sub(r"\bjualan\b", "menjual", q2))
    variants.append(re.sub(r"\bpromosi\b", "pemasaran", q2))
    variants.append(re.sub(r"\bpromosi\b", "marketing", q2))
    variants.append(re.sub(r"\bdigital marketing\b", "pemasaran digital", q2))

    # Variasi tiktok kadang kepisah
    if "tiktok" in q2:
        variants.append(q2.replace("tiktok", "tik tok"))

    # Workaround untuk kasus dokumen ada spasi aneh (misal: "fac ebook")
    if "facebook" in q2:
        variants.append(q2.replace("facebook", "fac ebook"))

    # Dedup + buang kosong
    out: List[str] = []
    seen = set()
    for v in variants:
        v = v.strip()
        if not v:
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)

    return out[:6]


def search_report(query: str, k: int = 5) -> dict:
    print(f"[TOOL] search_report called: query={query!r}, k={k}, db={DB_PATH}")
    if not DB_PATH.exists():
        return {"query": query, "results": [], "error": f"DB not found: {DB_PATH}"}

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(report_fts)").fetchall()]

    select_cols = ["chunk", "source", "page"]
    for extra in ["category", "section_title", "fr_number", "chunk_id"]:
        if extra in cols:
            select_cols.append(extra)

    select_sql = ", ".join(select_cols)

    def run(q: str):
        cur.execute(
            f"SELECT {select_sql} FROM report_fts "
            "WHERE report_fts MATCH ? "
            "ORDER BY bm25(report_fts) LIMIT ?",
            (q, k),
        )
        return cur.fetchall()

    # --- NEW: try expanded queries + fallback per variant ---
    query = _clean_query(query)
    variants = _expand_queries(query)
    all_rows = []

    # 1) coba query yang di-expand
    for qv in variants:
        try:
            all_rows.extend(run(qv))
        except sqlite3.OperationalError:
            continue

    # 2) kalau belum dapat, pakai fallback OR untuk tiap variant
    if not all_rows:
        for qv in variants:
            fb = _build_fallback_query(qv)
            if not fb:
                continue
            try:
                all_rows.extend(run(fb))
            except sqlite3.OperationalError:
                continue

    rows = all_rows

    # --- NEW: dedup results (biar tidak dobel karena banyak variant) ---
    results = []
    seen = set()
    for row in rows:
        item = dict(zip(select_cols, row))
        key = (
            item.get("source"),
            item.get("page"),
            item.get("chunk_id"),
            (item.get("chunk") or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)

        results.append({
            "text": item.get("chunk"),
            "source": item.get("source"),
            "page": item.get("page"),
            "category": item.get("category"),
            "section_title": item.get("section_title"),
            "fr_number": item.get("fr_number"),
            "chunk_id": item.get("chunk_id"),
        })

        if len(results) >= k:
            break

    conn.close()
    print(f"[TOOL] search_report hits={len(results)} variants={variants}")

    return {"query": query, "results": results}
