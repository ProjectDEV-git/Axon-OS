import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from constants import CONVERSATIONS_DB


class ConversationStore:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = str(CONVERSATIONS_DB)

        # Ensure directory exists with restricted permissions
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_db()
        # Restrict DB file permissions to owner-only (privacy: conversation history)
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _get_connection(self):
        """Return a per-thread SQLite connection, reusing if still open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        self._local.conn = conn
        return conn

    def _close_connection(self, conn):
        """Close the connection to release file descriptors.

        Daemon threads die without calling close(), leaking FDs. Each
        sqlite3.connect() is ~0.1ms, so the per-call overhead is negligible.
        """
        try:
            conn.close()
        except Exception:
            pass

    def close(self):
        """Explicitly close the per-thread connection (legacy compat)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def __del__(self) -> None:
        self.close()

    def _init_db(self):
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    system_prompt TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
            """)

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)"
            )
            conn.commit()
        finally:
            self._close_connection(conn)

    def create_conversation(self, system_prompt=None, title=None, conv_id=None):
        if not conv_id:
            conv_id = str(uuid.uuid4())
        if not title:
            title = f"New Chat ({datetime.now().strftime('%Y-%m-%d %H:%M')})"

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT INTO conversations (id, title, system_prompt) VALUES (?, ?, ?)",
                    (conv_id, title, system_prompt),
                )
                conn.commit()
            finally:
                self._close_connection(conn)
        return conv_id

    def add_message(self, conversation_id, role, content):
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
                )
                if not cursor.fetchone():
                    conn.execute(
                        "INSERT INTO conversations (id, title) VALUES (?, ?)",
                        (
                            conversation_id,
                            f"New Chat ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                        ),
                    )

                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                    (conversation_id, role, content),
                )
                conn.execute(
                    "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (conversation_id,),
                )
                conn.commit()
            finally:
                self._close_connection(conn)

    def get_messages(self, conversation_id):
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    "SELECT role, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                    (conversation_id,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                self._close_connection(conn)

    def list_conversations(self):
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    "SELECT id, title, system_prompt, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                self._close_connection(conn)

    def delete_conversation(self, conversation_id):
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
                conn.commit()
            finally:
                self._close_connection(conn)

    def update_title(self, conversation_id, title):
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (title, conversation_id),
                )
                conn.commit()
            finally:
                self._close_connection(conn)

    def search_messages(self, query):
        with self._lock:
            conn = self._get_connection()
            try:
                safe_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                cursor = conn.execute(
                    """
                    SELECT m.conversation_id, c.title as conversation_title, m.role, m.content, m.timestamp
                    FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE m.content LIKE ? ESCAPE '\\'
                    ORDER BY m.timestamp DESC
                """,
                    (f"%{safe_query}%",),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                self._close_connection(conn)
