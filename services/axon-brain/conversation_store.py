import os
import sqlite3
from datetime import datetime
import uuid

class ConversationStore:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.expanduser("~/.axon/conversations.db")
        
        # Ensure directory exists with restricted permissions
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()
        # Restrict DB file permissions to owner-only (privacy: conversation history)
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            # Create conversations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    system_prompt TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create messages table
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
            
            # Create index for faster lookups
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)")
            conn.commit()

    def create_conversation(self, system_prompt=None, title=None, conv_id=None):
        if not conv_id:
            conv_id = str(uuid.uuid4())
        if not title:
            title = f"New Chat ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
            
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, system_prompt) VALUES (?, ?, ?)",
                (conv_id, title, system_prompt)
            )
            conn.commit()
        return conv_id

    def add_message(self, conversation_id, role, content):
        with self._get_connection() as conn:
            # Check if conversation exists, if not create it
            cursor = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,))
            if not cursor.fetchone():
                self.create_conversation(conv_id=conversation_id)
                
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (conversation_id, role, content)
            )
            conn.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (conversation_id,)
            )
            conn.commit()

    def get_messages(self, conversation_id):
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT role, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_conversations(self):
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, title, system_prompt, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_conversation(self, conversation_id):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            conn.commit()

    def update_title(self, conversation_id, title):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (title, conversation_id)
            )
            conn.commit()

    def search_messages(self, query):
        with self._get_connection() as conn:
            # Search messages and return their conversation title + content snippet
            cursor = conn.execute("""
                SELECT m.conversation_id, c.title as conversation_title, m.role, m.content, m.timestamp 
                FROM messages m
                JOIN conversations c ON m.conversation_id = c.id
                WHERE m.content LIKE ?
                ORDER BY m.timestamp DESC
            """, (f"%{query}%",))
            return [dict(row) for row in cursor.fetchall()]
