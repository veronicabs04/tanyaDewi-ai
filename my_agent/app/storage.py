import os
import sqlite3
from datetime import datetime

class DatabaseSessionService:
    def __init__(self, db_path="data/chat.db"):
        self.db_path = db_path

        # pastikan folder ada
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
        cur = conn.cursor()

        # sessions sekarang punya user_id
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            conversation_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            period_key TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA table_info(sessions)")
        cols = [row[1] for row in cur.fetchall()]
        if "period_key" not in cols:
            cur.execute("ALTER TABLE sessions ADD COLUMN period_key TEXT")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES sessions(conversation_id)
                )
        """)

        conn.commit()
        conn.close()

    def create_session(self, conversation_id: str, user_id: int, period_key: str | None = None):
        conn = self._connect()
        cur = conn.cursor()
        if period_key is None:
            cur.execute(
                "INSERT INTO sessions (conversation_id, user_id, created_at) VALUES (?, ?, ?)",
                (conversation_id, int(user_id), datetime.utcnow().isoformat()),
            )
        else:
            cur.execute(
                "INSERT INTO sessions (conversation_id, user_id, period_key, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, int(user_id), period_key, datetime.utcnow().isoformat()),
            )
        conn.commit()
        conn.close()

    def get_session_by_user_and_period(self, user_id: int, period_key: str) -> str | None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT conversation_id
            FROM sessions
            WHERE user_id = ? AND period_key = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (int(user_id), period_key),
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def session_belongs_to_user(self, conversation_id: str, user_id: int) -> bool:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sessions WHERE conversation_id = ? AND user_id = ? LIMIT 1",
            (conversation_id, int(user_id)),
        )
        row = cur.fetchone()
        conn.close()
        return row is not None

    def add_message(self, conversation_id: str, role: str, content: str):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()

    def get_messages(self, conversation_id: str):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        )
        rows = cur.fetchall()
        conn.close()

        return [
            {"role": r[0], "content": r[1], "created_at": r[2]}
            for r in rows
        ]
