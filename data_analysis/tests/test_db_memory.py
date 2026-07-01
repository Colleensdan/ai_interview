"""In-memory DB layer: sharing, blob round-trip, and corruption rejection.

These guard the core data-safety property: the in-memory DB is a single shared
database across threads, and it can be saved to / restored from a blob without
ever losing or corrupting data.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

import db


def test_fresh_connections_share_the_same_memory_db(mem_db):
    c1 = db.connect()
    c1.execute("CREATE TABLE t(x INTEGER)")
    c1.execute("INSERT INTO t VALUES (1), (2), (3)")
    c1.commit()
    c1.close()

    c2 = db.connect(row_factory=True)
    assert c2.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"] == 3
    c2.close()


def test_data_survives_when_all_query_connections_close(mem_db):
    # The anchor must keep the shared DB alive after every per-query conn closes.
    c = db.connect()
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (42)")
    c.commit()
    c.close()
    # ... time passes, no open per-query connection ...
    c = db.connect()
    assert c.execute("SELECT x FROM t").fetchone()[0] == 42
    c.close()


def test_cross_thread_writes_are_visible(mem_db):
    db.connect().execute("CREATE TABLE t(x)")  # create in main thread
    main = db.connect()
    main.execute("CREATE TABLE IF NOT EXISTS t(x)")
    main.commit()

    def worker():
        w = db.connect()
        w.execute("INSERT INTO t VALUES (7)")
        w.commit()
        w.close()

    th = threading.Thread(target=worker)
    th.start()
    th.join()
    assert main.execute("SELECT x FROM t").fetchone()[0] == 7
    main.close()


def test_dump_and_load_blob_roundtrip(mem_db):
    c = db.connect()
    c.execute("CREATE TABLE code_definitions(code_name TEXT, definition TEXT)")
    c.execute("INSERT INTO code_definitions VALUES ('A: trust', 'v1 def')")
    c.commit()
    c.close()

    blob = db.dump_blob()
    assert isinstance(blob, bytes) and len(blob) > 0

    # Wipe the live DB (simulate a process restart) then restore from the blob.
    db._reset_anchor_for_tests()
    restored = db.connect()
    with pytest.raises(sqlite3.OperationalError):
        restored.execute("SELECT * FROM code_definitions")  # gone after reset
    restored.close()

    db.load_blob(blob)
    c = db.connect(row_factory=True)
    row = c.execute("SELECT * FROM code_definitions").fetchone()
    assert row["code_name"] == "A: trust"
    assert row["definition"] == "v1 def"
    c.close()


def test_load_blob_visible_to_sibling_connections(mem_db):
    # backup() into shared-cache (not deserialize) so ALL connections see it.
    src = sqlite3.connect(":memory:")
    src.execute("CREATE TABLE t(x)")
    src.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
    src.commit()
    blob = src.serialize()
    src.close()

    db.load_blob(blob)
    # A brand-new connection (a "sibling") must see the loaded data.
    c = db.connect()
    assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    c.close()


def test_load_blob_accepts_wal_mode_source_file(mem_db, tmp_path):
    # A real on-disk WAL database (like the legacy coding_seed.sqlite) must load,
    # even though sqlite3.deserialize() rejects WAL images directly.
    src = tmp_path / "wal.sqlite"
    con = sqlite3.connect(str(src))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE kappa(code_name TEXT, kappa REAL)")
    con.execute("INSERT INTO kappa VALUES ('A: trust', 0.87)")
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    blob = src.read_bytes()
    assert blob[18] == 2 and blob[19] == 2  # confirm it's WAL on disk

    db.load_blob(blob)  # must not raise
    c = db.connect(row_factory=True)
    assert c.execute("SELECT kappa FROM kappa WHERE code_name='A: trust'").fetchone()["kappa"] == 0.87
    c.close()


def test_load_blob_rejects_corrupt_blob_and_leaves_db_intact(mem_db):
    # Seed good data.
    c = db.connect()
    c.execute("CREATE TABLE keep(x)")
    c.execute("INSERT INTO keep VALUES ('safe')")
    c.commit()
    c.close()

    good = db.dump_blob()
    corrupt = bytearray(good)
    # Corrupt the SQLite header/pages so integrity_check fails.
    for i in range(100, min(len(corrupt), 4000)):
        corrupt[i] ^= 0xFF

    with pytest.raises(db.IntegrityError):
        db.load_blob(bytes(corrupt))

    # The live DB must be untouched — no partial clobber.
    c = db.connect()
    assert c.execute("SELECT x FROM keep").fetchone()[0] == "safe"
    c.close()
