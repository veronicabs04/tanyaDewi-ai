import os, sqlite3, json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "knowledge.db"
CHUNKS = PROJECT_ROOT / "chunks_fr_ai.jsonl"

os.makedirs(DB_PATH.parent, exist_ok=True)

conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()

cur.execute("DROP TABLE IF EXISTS report_fts")
cur.execute("""
CREATE VIRTUAL TABLE report_fts USING fts5(
  chunk,
  source,
  page,
  section_title,
  fr_number,
  chunk_id
)
""")

with open(CHUNKS, encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)
        cur.execute(
            "INSERT INTO report_fts(chunk, source, page, section_title, fr_number, chunk_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                row["text"],
                row.get("source") or "unknown",
                row.get("page"),
                row.get("section_title"),
                row.get("fr_number"),
                row.get("id"),
            )
        )

conn.commit()
conn.close()
print("✅ Ingest selesai:", DB_PATH)
