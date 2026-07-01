#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import time
from array import array
from pathlib import Path

import dbus
import sqlite_vec

# Ensure we can load axon_logger
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from axon_logger import configure_app_logger

    logger = configure_app_logger(__name__)
except ImportError:
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("axon-file-indexer")

from constants import AXON_DIR

DB_PATH = AXON_DIR / "semantic_search.db"


class FileIndexer:
    def __init__(self):
        AXON_DIR.mkdir(parents=True, exist_ok=True)
        self.session_bus = dbus.SessionBus()
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)

        self.init_db()
        self.watch_dirs = [
            Path.home() / "Documents",
            Path.home() / "Notes",
            Path.home() / "Projects",
        ]

        logger.info(f"File Indexer initialized. Monitoring: {[str(d) for d in self.watch_dirs]}")

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None  # type: ignore[assignment]

    def __del__(self):
        self.close()

    def init_db(self):
        cursor = self.conn.cursor()
        # Normal metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE,
                mtime REAL,
                content TEXT
            )
        """)
        # Vector virtual table (768 dimensions for nomic-embed-text)
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
                embedding float[768]
            )
        """)
        self.conn.commit()

    def get_brain_embedding(self, text):
        try:
            brain_obj = self.session_bus.get_object("org.axonos.Brain", "/org/axonos/Brain")
            brain_interface = dbus.Interface(brain_obj, "org.axonos.Brain")
            # Use general model or default for embedding
            emb_json = brain_interface.GetEmbeddings(text, "")
            emb = json.loads(emb_json)
            if isinstance(emb, list) and emb and isinstance(emb[0], (int, float)):
                return emb
        except Exception as e:
            logger.error(f"Failed to fetch embedding: {e}")
        return None

    def index_file(self, file_path):
        try:
            p = Path(file_path)
            if not p.exists() or not p.is_file():
                return

            try:
                if p.stat().st_size > 512 * 1024:
                    return
            except OSError:
                return

            mtime = p.stat().st_mtime

            # Check DB
            cursor = self.conn.cursor()
            cursor.execute("SELECT id, mtime FROM files WHERE path = ?", (str(p),))
            row = cursor.fetchone()

            if row and row[1] >= mtime:
                # No change
                return

            logger.info(f"Indexing file: {p}")

            # Read first 1500 chars (reasonable context chunk)
            try:
                content = p.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                return

            if not content:
                return

            chunk = content[:1500]
            emb = self.get_brain_embedding(chunk)
            if not emb:
                logger.warning(f"Could not generate embedding for {p}")
                return

            emb_bytes = array("f", emb).tobytes()

            if row:
                doc_id = row[0]
                cursor.execute(
                    "UPDATE files SET mtime = ?, content = ? WHERE id = ?", (mtime, chunk, doc_id)
                )
                cursor.execute(
                    "UPDATE vec_items SET embedding = ? WHERE rowid = ?", (emb_bytes, doc_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO files (path, mtime, content) VALUES (?, ?, ?)",
                    (str(p), mtime, chunk),
                )
                doc_id = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO vec_items (rowid, embedding) VALUES (?, ?)", (doc_id, emb_bytes)
                )

            self.conn.commit()
            logger.info(f"Successfully indexed: {p}")
        except Exception:
            logger.exception(f"Error indexing {file_path}:")

    def remove_deleted_files(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id, path FROM files")
            rows = cursor.fetchall()
            for row in rows:
                doc_id, path_str = row
                if not os.path.exists(path_str):
                    logger.info(f"Removing deleted file from index: {path_str}")
                    cursor.execute("DELETE FROM files WHERE id = ?", (doc_id,))
                    cursor.execute("DELETE FROM vec_items WHERE rowid = ?", (doc_id,))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error purging index: {e}")

    def scan_and_index(self):
        self.remove_deleted_files()

        valid_exts = {".txt", ".md", ".py", ".json", ".js", ".html", ".css", ".sh", ".c", ".cpp"}
        for watch_dir in self.watch_dirs:
            if not watch_dir.exists():
                continue
            for root, _, files in os.walk(watch_dir):
                # Ignore hidden directories like .git
                if "/." in root or root.split("/")[-1].startswith("."):
                    continue
                for f in files:
                    p = Path(root) / f
                    if p.suffix in valid_exts:
                        self.index_file(p)

    def run_loop(self):
        while True:
            try:
                self.scan_and_index()
            except Exception as e:
                logger.error(f"Error in index loop: {e}")
            time.sleep(30)


if __name__ == "__main__":
    indexer = FileIndexer()
    logger.info("Starting background file scan loop...")
    indexer.run_loop()
