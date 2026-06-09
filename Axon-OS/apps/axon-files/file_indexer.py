#!/usr/bin/env python3
"""Axon Files — Directory crawler, content parser, DBus embedding fetcher, and SQLite database manager."""

import os
import sqlite3
import json
import math
from pathlib import Path
from datetime import datetime

import dbus
try:
    import dbus.mainloop.glib
    dbus.mainloop.glib.threads_init()
except Exception:
    pass

# Helper to format file size
def format_size(size_in_bytes):
    if size_in_bytes is None:
        return ""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.1f} {unit}" if unit != 'B' else f"{size_in_bytes} B"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.1f} PB"

# Helper to format timestamp
def format_timestamp(timestamp):
    if timestamp is None:
        return ""
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%b %d, %Y %H:%M")
    except Exception:
        return ""

def cosine_similarity(v1, v2):
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    magnitude1 = math.sqrt(sum(a * a for a in v1))
    magnitude2 = math.sqrt(sum(b * b for b in v2))
    if magnitude1 == 0.0 or magnitude2 == 0.0:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)

def fetch_embedding_dbus(prompt: str) -> list:
    """Generates embedding vector for a given prompt using Ollama via D-Bus."""
    try:
        bus = dbus.SessionBus()
        brain_obj = bus.get_object('org.axonos.Brain', '/org/axonos/Brain')
        # Call GetEmbeddings(prompt, model)
        embeddings_json = brain_obj.GetEmbeddings(prompt, "", dbus_interface='org.axonos.Brain')
        embedding = json.loads(embeddings_json)
        if isinstance(embedding, list):
            return embedding
        elif isinstance(embedding, dict) and "error" in embedding:
            print(f"D-Bus embedding error: {embedding['error']}")
            return []
    except Exception as e:
        print(f"Error fetching embedding via D-Bus: {e}")
    return []

def get_all_files(roots):
    """Recursively walks roots and returns list of file paths while excluding heavy folders."""
    all_files = []
    ignored_dirs = {
        'node_modules', '__pycache__', 'venv', 'env', 'build', 'dist', 
        'target', '.git', '.cache', '.axon', '.gemini', '.local', 'tmp',
        'flatpak', 'snap', 'cache'
    }
    for root in roots:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            continue
        if root_path.is_file():
            if not root_path.name.startswith('.'):
                all_files.append(root_path)
            continue
            
        for dirpath, dirnames, filenames in os.walk(root_path):
            # Prune ignored directories in place
            dirnames[:] = [d for d in dirnames if d not in ignored_dirs and not d.startswith('.')]
            for f in filenames:
                if f.startswith('.'):
                    continue
                all_files.append(Path(dirpath) / f)
    return all_files

class FileIndexer:
    def __init__(self, db_path=None):
        if db_path is None:
            self.db_path = Path.home() / ".axon" / "files_index.db"
        else:
            self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE,
                file_name TEXT,
                file_type TEXT,
                file_size INTEGER,
                last_modified REAL,
                content_summary TEXT,
                embedding TEXT
            )
        """)
        conn.commit()
        conn.close()

    def get_indexed_count(self) -> int:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def scan_directories(self, root_dirs, progress_callback=None, cancel_event=None):
        """
        Walks root_dirs, reads content_summaries of text files, generates
        embeddings via DBus (GetEmbeddings), and stores metadata/embeddings in SQLite.
        """
        all_files = get_all_files(root_dirs)
        total_files = len(all_files)
        
        # Load existing db metadata to avoid scanning unchanged files
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, file_size, last_modified FROM files")
        db_files = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
        conn.close()
        
        visited_paths = set()
        indexed_count = 0
        
        for idx, file_path in enumerate(all_files):
            if cancel_event and cancel_event.is_set():
                break
                
            path_str = str(file_path)
            visited_paths.add(path_str)
            
            try:
                stat = file_path.stat()
                file_size = stat.st_size
                last_modified = stat.st_mtime
            except Exception:
                continue
                
            # Check if unchanged
            if path_str in db_files:
                db_size, db_mtime = db_files[path_str]
                if db_size == file_size and abs(db_mtime - last_modified) < 0.01:
                    indexed_count += 1
                    if progress_callback:
                        progress_callback(path_str, indexed_count, total_files)
                    continue
            
            # Re-index or index new file
            file_name = file_path.name
            file_type = file_path.suffix.lower().lstrip('.')
            
            content_summary = ""
            text_extensions = {
                'txt', 'py', 'md', 'js', 'json', 'csv', 'html', 'css', 
                'rs', 'c', 'cpp', 'h', 'sh', 'xml', 'yaml', 'yml', 
                'ini', 'cfg', 'toml', 'go', 'java', 'ts', 'tsx', 'jsx'
            }
            
            if file_type in text_extensions:
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content_summary = f.read(2000)
                except Exception:
                    pass
            
            # Prompt context
            embedding_prompt = f"File: {file_name}\nPath: {path_str}\nType: {file_type}\nSummary: {content_summary}"
            
            # D-Bus call to brain
            embedding_list = fetch_embedding_dbus(embedding_prompt)
            embedding_json = json.dumps(embedding_list) if embedding_list else None
            
            # Save to database
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO files 
                (file_path, file_name, file_type, file_size, last_modified, content_summary, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (path_str, file_name, file_type, file_size, last_modified, content_summary, embedding_json))
            conn.commit()
            conn.close()
            
            indexed_count += 1
            if progress_callback:
                progress_callback(path_str, indexed_count, total_files)
                
        # Cleanup deleted files from Database
        if not (cancel_event and cancel_event.is_set()):
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT file_path FROM files")
            all_db_paths = [row[0] for row in cursor.fetchall()]
            
            deleted_paths = []
            for db_path in all_db_paths:
                db_path_obj = Path(db_path)
                under_roots = False
                for r in root_dirs:
                    r_path = Path(r).expanduser().resolve()
                    try:
                        if r_path in db_path_obj.parents or r_path == db_path_obj:
                            under_roots = True
                            break
                    except Exception:
                        pass
                
                if under_roots and db_path not in visited_paths:
                    deleted_paths.append(db_path)
            
            if deleted_paths:
                cursor.executemany("DELETE FROM files WHERE file_path = ?", [(p,) for p in deleted_paths])
                conn.commit()
            conn.close()

    def search_local(self, query_text, use_semantic=False, limit=50):
        """Searches files via substring or semantic indexing."""
        if not query_text.strip():
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path, file_name, file_type, file_size, last_modified, content_summary
                FROM files
                ORDER BY last_modified DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
            
        if use_semantic:
            query_emb = fetch_embedding_dbus(query_text)
            if not query_emb:
                # Fallback to normal search if D-Bus fails
                use_semantic = False
                
        if use_semantic:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path, file_name, file_type, file_size, last_modified, content_summary, embedding
                FROM files
                WHERE embedding IS NOT NULL
            """)
            rows = cursor.fetchall()
            conn.close()
            
            results = []
            for row in rows:
                try:
                    emb_list = json.loads(row['embedding'])
                    if not emb_list:
                        continue
                    sim = cosine_similarity(query_emb, emb_list)
                    results.append({
                        'id': row['id'],
                        'file_path': row['file_path'],
                        'file_name': row['file_name'],
                        'file_type': row['file_type'],
                        'file_size': row['file_size'],
                        'last_modified': row['last_modified'],
                        'content_summary': row['content_summary'],
                        'similarity': sim
                    })
                except Exception:
                    pass
            
            results.sort(key=lambda x: x['similarity'], reverse=True)
            return results[:limit]
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query_like = f"%{query_text}%"
            cursor.execute("""
                SELECT id, file_path, file_name, file_type, file_size, last_modified, content_summary
                FROM files
                WHERE file_name LIKE ? OR file_path LIKE ? OR content_summary LIKE ?
                ORDER BY last_modified DESC
                LIMIT ?
            """, (query_like, query_like, query_like, limit))
            rows = cursor.fetchall()
            conn.close()
            
            results = []
            for row in rows:
                item = dict(row)
                item['similarity'] = 0.0
                results.append(item)
            return results
