import time
import os
import sqlite3

db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE chunks (path TEXT, mtime REAL)")
db.execute("CREATE INDEX idx_chunks_path ON chunks(path)")

for i in range(10000):
    db.execute("INSERT INTO chunks (path, mtime) VALUES (?, ?)", (f"/home/user/file_{i}.txt", 12345.6))
db.commit()

paths = [f"/home/user/file_{i}.txt" for i in range(10000)]

start_time = time.time()
for p in paths:
    row = db.execute("SELECT mtime FROM chunks WHERE path=? LIMIT 1", (p,)).fetchone()
    if row:
        pass
end_time = time.time()
print(f"N+1 approach took: {end_time - start_time:.4f}s")

start_time = time.time()
known_mtimes = {row[0]: row[1] for row in db.execute("SELECT path, mtime FROM chunks GROUP BY path")}
for p in paths:
    row = known_mtimes.get(p)
    if row:
        pass
end_time = time.time()
print(f"Pre-fetch approach took: {end_time - start_time:.4f}s")
