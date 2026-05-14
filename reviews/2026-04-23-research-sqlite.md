# SQLite + FastAPI Concurrency Research

## Summary for the Reviewer

1. **No concurrency bug in current code.** Each sync handler opens a new connection, uses it on a single thread, then closes it. With `check_same_thread=True` (default), this is safe—each connection lives and dies on one thread. The process-level lockfile ensures only one backend process touches the logbook at all times.

2. **WAL is safe for multiple readers + single writer.** WAL mode allows concurrent readers (from different threads) to see consistent snapshots while a writer appends to the separate `-wal` file. When two threads in the same process both try to write, SQLite's locking mechanism blocks one until the other finishes—but the default `busy_timeout=0` means the blocked thread gets `SQLITE_BUSY` immediately instead of retrying. This is reachable but unlikely given the single-process design.

3. **Connection-per-request pattern is standard for FastAPI + sync SQLite.** It avoids the GIL-related complexity of shared connections and aligns with Starlette's threadpool model. On a single-user desktop app with dozens of requests per minute, the cost is negligible (a few milliseconds per open/close).

4. **Autocommit mode + future multi-statement work is a gotcha.** Today every operation is a single statement in autocommit mode—safe and correct. But if a future service writes multiple statements atomically, `isolation_level=None` won't roll back on error unless you explicitly wrap with `BEGIN/COMMIT`. The code has no `busy_timeout` set, so contentious writes in high-concurrency contexts would fail fast rather than wait.

5. **Cloud sync (Dropbox, iCloud) can corrupt the DB if `.wal` and `.shm` files don't sync together.** WAL's shared-memory index file (`.shm`) and the append-only log (`.wal`) must be synchronized atomically with the main DB file. Partial syncs leave the DB corrupt. This is outside the scope of v0.1 but worth documenting for future deployment guidance.

---

## 1. sqlite3 Thread-Safety

### Connection Thread Affinity

CPython's `sqlite3.connect()` defaults to `check_same_thread=True`, which enforces that a connection is used only by the thread that created it. Raising `ProgrammingError` if any other thread accesses the connection.

**Code Pattern (current):** Each service function calls `open_index(logbook_root)`, which calls `sqlite3.connect(str(path), isolation_level=None)` without explicitly setting `check_same_thread`. This means `check_same_thread=True` is in effect.

**Thread Model (current):** Every service call runs in a Starlette sync handler (sync FastAPI route). Starlette dispatches sync handlers via Anyio's `to_thread.run_sync()`, which runs each handler on a threadpool worker. Each worker thread executes exactly one handler at a time, so a single connection opened in a handler stays on that handler's thread and closes in the same function's `finally` block.

**Verdict:** Safe. Each connection satisfies the thread affinity requirement by construction.

### Multiple Threads, Single DB File

With WAL mode, multiple processes (or threads) can each open their own connection to the same database file and read concurrently. Each thread maintains its own connection object and connection state. SQLite coordinates via the `.wal` file (append-only log) and the `.shm` file (shared-memory index).

**Current constraint:** The process-level `FileLock` in `lockfile.py` ensures only one uvicorn process touches the logbook at any moment. Within a single process, sync handlers run on a threadpool (default limit: 40 concurrent threads per AnyIO's capacity limiter). All threads can open the same DB file because:

- Each has its own `sqlite3.Connection` object (not shared).
- WAL allows readers to run concurrently while one writer appends.
- The OS file layer and sqlite3 locking (via WAL's `.shm` index) coordinate atomicity.

**Verdict:** Safe. WAL's design permits this pattern.

### Connection Lifetime and Durability Trade-offs

Opening a connection triggers a recovery pass if the previous process crashed mid-transaction. Closing a connection (cleanly) triggers a WAL checkpoint and cleanup of `.wal` and `.shm` files.

**Current code:** Every service function opens, executes (typically) one statement, then closes in a finally block. This means:
- Each request opens and closes the DB independently.
- Checkpoints run frequently (potentially on every request if WAL grows large).
- `.wal` and `.shm` files are cleaned up aggressively.

**Verdict:** Simple and correct. The frequent checkpoint overhead is negligible for a single-user app. No long-lived connections to manage.

---

## 2. WAL Mode Concurrency Semantics

### Readers vs. Writers

Per SQLite's WAL documentation: "Readers do not block writers and a writer does not block readers." Multiple readers can see consistent snapshots from different points in the WAL. A writer appends to the WAL; only one writer can run at a time (enforced by file locking on the WAL file itself).

### Two Threads Both Writing: The Busy Timeout Gotcha

If two threads in the same process both execute a write (INSERT, UPDATE, DELETE) at the same moment:
1. Thread A acquires the WAL write lock.
2. Thread B attempts the same, gets `SQLITE_BUSY`.
3. With `busy_timeout=0` (default), Thread B immediately raises `sqlite3.OperationalError: database is locked` instead of retrying.

**Is this reachable?** Only if two service handlers run truly concurrently (on the threadpool). Today:
- The `FileLock` prevents multiple processes from writing.
- Within a process, only one socket handler can write at a time (handlers execute serially on the threadpool when writes collide due to Python's scheduling).
- In practice, with typical request rates (dozens per minute), write contention is zero.

**Risk level:** Very low for v0.1. Unset `busy_timeout` becomes a problem only if you:
- Implement async writes (today: sync handlers).
- Have truly parallel write handlers on the threadpool (40-thread capacity limiter).
- Expect waits instead of immediate rejection.

**Recommendation for future phases:** If multi-threaded async writes arrive, consider adding `conn.execute("PRAGMA busy_timeout = 5000")` (5-second retry window) after opening.

### WAL-Reset Bug (SQLite 3.51.3+)

SQLite fixed a rare data corruption race (the WAL-reset bug) in versions 3.51.3 (2026-03-13) and backports to 3.44.6, 3.50.7. The bug affects:
- Two or more connections on the same file in separate threads/processes.
- Checkpoint + write operations happening simultaneously with tight timing.
- Data race probability: extremely low, unreproducible in normal use.

**Current relevance:** The code does not trigger the exact conditions (checkpoints are passive and infrequent), but ensure the runtime Python sqlite3 module is linked to SQLite 3.51.3 or later if this is a production concern.

---

## 3. Connection-Per-Operation: Cost + Precedent

### Actual Cost of `sqlite3.connect()`

Opening a connection to an existing DB file:
1. File is opened (single syscall if already cached).
2. WAL header is read; if the DB is in WAL mode, the WAL-index is mapped or created.
3. If the previous process crashed, a recovery pass may run (negligible for a well-formed DB).
4. PRAGMAs are set (connection-level; not persistent on disk).

For a local file on SSD, this takes microseconds to a few milliseconds.

### PRAGMA Overhead

The code sets `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` on every open:
- `journal_mode=WAL`: First time this runs, it converts the DB to WAL mode and persists that in the DB header. Subsequent opens see `WAL` already active and skip the conversion (PRAGMA is a no-op).
- `synchronous=NORMAL`: A connection-level setting, not persistent; must be set on every open. Cheap (no I/O).

### Precedent in FastAPI + sqlite3

**FastAPI official guidance** (SQL Databases docs) uses SQLAlchemy, which pools connections. For raw sqlite3:
- Datasette (Simon Willison's production tool for exploring SQLite) uses one connection per request in its sync context and manages read concurrency via WAL.
- Typical patterns avoid connection pooling for SQLite because WAL makes concurrent access safe without a pool.

**For this codebase (single-user desktop app):** Connection-per-request is appropriate. The cost is zero; the simplicity is high.

---

## 4. Autocommit and Multi-Statement Transactions

### Current Pattern

With `isolation_level=None`, every statement is its own implicit transaction. A single INSERT, UPDATE, or DELETE commits immediately.

```python
conn.execute("INSERT INTO jumps (...) VALUES (...)")  # Auto-commits
```

This is safe and correct for single-statement writes (all service functions today).

### Future Gotcha: Multiple Statements

If a future service function tries to do two statements atomically:

```python
# WRONG: not atomic in autocommit mode
conn.execute("INSERT INTO jumps (...) VALUES (...)")
conn.execute("INSERT INTO equipment (...) VALUES (...)")
# If the second fails, the first is already committed
```

**The correct idiom (when `isolation_level=None`):**

```python
# RIGHT: explicit transaction
conn.execute("BEGIN")
try:
    conn.execute("INSERT INTO jumps (...) VALUES (...)")
    conn.execute("INSERT INTO equipment (...) VALUES (...)")
    conn.execute("COMMIT")
except Exception:
    conn.execute("ROLLBACK")
    raise
```

**Incorrect idiom (won't work with `isolation_level=None`):**

```python
# WRONG: with conn: does nothing in autocommit mode
with conn:
    conn.execute("INSERT ...")
    conn.execute("INSERT ...")
```

The `with` statement only commits/rolls back if `isolation_level is not None`. In autocommit mode, it's a no-op.

**Recommendation:** Document this in code comments before any multi-statement work is added. Consider adding a helper in the service layer:

```python
def atomic_transaction(conn: sqlite3.Connection):
    """Context manager for multi-statement atomicity in autocommit mode."""
    conn.execute("BEGIN")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
```

---

## 5. FastAPI-Specific Gotchas

### AnyIO Thread Limiter Capacity

Starlette's sync handler dispatch uses AnyIO's `to_thread.run_sync()`, which enforces a default capacity limit of **40 concurrent threads**. This means at most 40 sync handlers can run in parallel.

If 40 handlers are running and a 41st request arrives, it queues until a handler finishes.

**Current relevance:** With a single-user desktop app and dozens of requests per minute, you'll never hit this. If you scale to hundreds of concurrent requests, you'd need to increase the limit:

```python
from anyio import to_thread
to_thread.current_default_thread_limiter().total_tokens = 100  # in lifespan
```

### Multiple Connections Under Load

With 40 concurrent threads, you could have up to 40 open connections to the same DB file (each handler opens its own). SQLite's WAL is designed for this:
- Readers get consistent snapshots from the WAL.
- Only one writer blocks the others; readers continue.
- The `.shm` index coordinates this efficiently.

**Verdict:** Safe. WAL is designed exactly for this pattern.

### No Known FastAPI + sqlite3 Issues

The FastAPI issue tracker has no specific known issues with raw sqlite3 + sync handlers. The Datasette project (widely used, production-grade) uses exactly this pattern.

---

## 6. WAL Checkpoint Behavior and Backup

### Automatic Checkpointing

By default, SQLite checkpoints when the WAL reaches 1000 pages (~4 MB). The code does not set `PRAGMA wal_autocheckpoint`, so 1000 pages is the default.

**What happens:**
- When a commit pushes WAL over 1000 pages, a checkpoint is triggered (same thread).
- Checkpoint moves pages from WAL back to the main DB file and syncs to disk.
- If a reader is active, checkpoint pauses at that reader's end-mark (can't overwrite pages the reader needs).
- If multiple readers are present, the checkpoint may run to completion only when all readers close.

**Clean vs. crash exit:**
- Clean exit: final close() triggers a checkpoint and deletes `.wal` and `.shm`.
- Crash (no close): `.wal` and `.shm` remain on disk. Next open runs recovery.

### Cloud Sync Implications

WAL requires three files to stay synchronized:
- `index.sqlite` (main DB file)
- `index.sqlite-wal` (write-ahead log)
- `index.sqlite-shm` (shared-memory index)

If these sync out of order to Dropbox or iCloud:
1. Main DB syncs first, `.wal` and `.shm` are lost → DB appears in pre-WAL state, corrupted.
2. `.wal` syncs first → orphaned log, DB reader sees inconsistency.

**Solution (not in v0.1 scope):** Cloud backup should disable WAL before syncing:

```python
conn.execute("PRAGMA journal_mode=DELETE")  # back out of WAL
conn.close()  # checkpoint and clean up
# Now sync to cloud
```

Or use Litestream (a dedicated WAL replication tool by the SQLite creator) for consistent snapshots.

---

## Citations

- [SQLite Write-Ahead Logging (WAL) - SQLite Documentation](https://www.sqlite.org/wal.html)
- [SQLite Thread Safety - SQLite Documentation](https://www.sqlite.org/threadsafe.html)
- [Python sqlite3 Module - Python 3.12+ Documentation](https://docs.python.org/3/library/sqlite3.html)
- [Thread Pool - Starlette Documentation](https://starlette.dev/threadpool/)
- [Working with Threads - AnyIO 4.13.0 Documentation](https://anyio.readthedocs.io/en/stable/threads.html)
- [FastAPI - SQL (Relational) Databases](https://fastapi.tiangolo.com/how-to/sql-databases/)
- [Datasette - An open source multi-tool for exploring and publishing data](https://datasette.io)
- [Simon Willison on Python and SQLite](https://simonwillison.net/tags/python+sqlite/)
- [SQLite Performance Tuning - phiresky's blog](https://phiresky.github.io/blog/2020/sqlite-performance-tuning/)
- [How SQLite Scales Read Concurrency - The Fly Blog](https://fly.io/blog/sqlite-internals-wal/)
