"""Bootstrap the Loading Agent World Model SQLite schema.

Uses Python's standard-library sqlite3 module; no pip install is required.
By default creates world_model.db beside this script. Existing databases are
not deleted or replaced. This is the v1 bootstrap; later schema changes should
be implemented through explicit migrations.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_NAME = "initial_world_model_schema"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).resolve().with_name("world_model.db"),
        help="database file path (default: world_model.db beside this script)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().with_name("schema.sql"),
        help="schema SQL file path (default: schema.sql beside this script)",
    )
    args = parser.parse_args()

    if not args.schema.is_file():
        parser.error(f"Schema file not found: {args.schema}")

    schema_sql = args.schema.read_text(encoding="utf-8")
    checksum = hashlib.sha256(schema_sql.encode("utf-8")).hexdigest()
    db_path = args.db.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    existed_before = db_path.exists()
    conn: sqlite3.Connection | None = None

    try:
        conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")

        current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current_version} is newer than this initializer "
                f"(supports {SCHEMA_VERSION}); refusing to downgrade it."
            )

        conn.executescript(schema_sql)
        conn.execute(
            """INSERT OR IGNORE INTO wm_schema_migrations
               (version, name, checksum) VALUES (?, ?, ?)""",
            (SCHEMA_VERSION, SCHEMA_NAME, checksum),
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        tables = [
            row[0]
            for row in conn.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table' AND name LIKE 'wm_%' ORDER BY name"""
            ).fetchall()
        ]
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        print(f"Failed to initialise World Model database: {exc}")
        return 1
    finally:
        if conn is not None:
            conn.close()

    action = "Initialised" if not existed_before else "Verified/updated schema for"
    print(f"{action}: {db_path}")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Tables: {', '.join(tables)}")
    print("Integrity check: ok")
    print("No world data was inserted; this is an empty schema ready for the SQLite adapter.")
    print("Note: index.ts remains in-memory until its persistence adapter is implemented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
