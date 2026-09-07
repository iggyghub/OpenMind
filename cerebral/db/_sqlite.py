import sqlite3
from pathlib import Path


def connect(path: str | Path, **kwargs) -> sqlite3.Connection:
    """Open a connection to an SQLite database with WAL mode and a generous busy_timeout.

    Prevents 'database is locked' errors under concurrent writes by switching
    to Write-Ahead Logging (WAL) and waiting up to 20 seconds for locks to clear.
    All PRAGMAs are applied immediately upon connection.
    """
    if isinstance(path, Path):
        path = str(path)
    if path != ":memory:" and not Path(path).parent.exists():
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    # sqlite3.connect accepts a `timeout` argument (seconds) for the initial
    # handshake. We default to 5s; the internal busy_timeout handles ongoing
    # lock contention up to 20s.
    con = sqlite3.connect(path, timeout=5.0, check_same_thread=False, **kwargs)

    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA busy_timeout=20000;")
    con.commit()
    return con
