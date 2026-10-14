import os
import sqlite3
import tempfile
import threading
import time
import unittest

from cerebral.db._sqlite import connect


class TestSQLiteWALHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = tempfile.mktemp(suffix=".db")

    def tearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_wal_mode_active(self) -> None:
        con = connect(self.db_path)
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode, "wal")
        con.close()

    def test_busy_timeout_set(self) -> None:
        con = connect(self.db_path)
        timeout = con.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout, 20000)
        con.close()

    def test_concurrent_writes_no_lock_error(self) -> None:
        """Two connections writing concurrently should not raise 'database is locked'
        within the 5-second window thanks to the 20-second busy_timeout."""
        errors = []

        def writer(conn: sqlite3.Connection, idx: int) -> None:
            try:
                for _ in range(50):
                    conn.execute(
                        "INSERT INTO t (val) VALUES (?)", (idx,)
                    )
                    conn.commit()
                    time.sleep(0.02)
            except sqlite3.OperationalError as e:
                errors.append(str(e))

        con1 = connect(self.db_path)
        con2 = connect(self.db_path)

        con1.execute("CREATE TABLE t (val INTEGER)")
        con1.commit()

        t1 = threading.Thread(target=writer, args=(con1, 1))
        t2 = threading.Thread(target=writer, args=(con2, 2))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        con1.close()
        con2.close()

        self.assertEqual(errors, [], f"Got database locked errors: {errors}")


if __name__ == "__main__":
    unittest.main()
