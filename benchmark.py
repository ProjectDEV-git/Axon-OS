import time
import os
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, 'services/axon-search')
import search_service

db = search_service.open_db()
db.execute("DELETE FROM chunks")
db.commit()

print("Inserting dummy records...")
for i in range(5000):
    db.execute("INSERT INTO chunks(path, mtime, chunk_idx, text) VALUES (?, ?, ?, ?)", (f"/tmp/fake_{i}", 1.0, 0, "text"))
db.commit()

paths = [f"/tmp/fake_{i}" for i in range(5000)]

start = time.time()
for p in paths:
    row = db.execute("SELECT mtime FROM chunks WHERE path=? LIMIT 1", (p,)).fetchone()
end1 = time.time()

start2 = time.time()
mtimes = {row[0]: row[1] for row in db.execute("SELECT path, mtime FROM chunks GROUP BY path").fetchall()}
for p in paths:
    m = mtimes.get(p)
end2 = time.time()

print(f"N+1 queries: {end1 - start:.4f} s")
print(f"Prefetch:    {end2 - start2:.4f} s")

# Cleanup
db.close()
