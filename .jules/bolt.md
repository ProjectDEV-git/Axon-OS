## 2024-05-18
- Performance optimization: eliminated N+1 query in `search_service.py` file indexing loop by pre-fetching `mtime` into a dictionary. This optimization yielded a 2.5x speed up.
