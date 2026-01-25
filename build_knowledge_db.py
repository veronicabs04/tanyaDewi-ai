import sqlite3
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "knowledge.db"
JSONL_PATH = PROJECT_ROOT / "chunks_fr_ai.jsonl"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print("🗑️ Drop old table (if exists)")
cur.execute("DROP TABLE IF EXISTS report_fts")

print("🆕 Create FTS table")
cur.execute("""
CREATE VIRTUAL TABLE report_fts USING fts5(
  chunk,
  source,
  page,
  section_title,
  fr_number,
  chunk_id,
  category
)
""")

print("📥 Insert chunks")
with open(JSONL_PATH, encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)
        cur.execute(
            """
            INSERT INTO report_fts
            (chunk, source, page, section_title, fr_number, chunk_id, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["text"],
                row["source"],
                row["page"],
                row.get("section_title"),
                row.get("fr_number"),
                row["id"],
                row.get("category"),
            )
        )

conn.commit()
conn.close()

print("✅ knowledge.db rebuilt successfully")
