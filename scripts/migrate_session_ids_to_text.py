
import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# Add project root directory to sys.path
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    """Connect to the physical PostgreSQL database."""
    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT", "5432"))
    dbname = os.getenv("POSTGRES_DB") or os.getenv("DB_NAME", "postgres")
    user = os.getenv("POSTGRES_USER") or os.getenv("DB_USER", "zetaris")
    password = (
        os.getenv("POSTGRES_PASSWORD")
        or os.getenv("DB_PASSWORD")
    )

    if not password:
        raise ValueError(
            "Database password missing. Set POSTGRES_PASSWORD in .env."
        )

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        cursor_factory=RealDictCursor,
    )


def run_migration():
    """Convert session IDs to TEXT while preserving referential integrity."""
    conn = None

    try:
        print("Connecting to PostgreSQL...")
        conn = get_db_connection()

        with conn.cursor() as cur:
            # Confirm the target before changing anything.
            cur.execute("SELECT current_database() AS database_name")
            database_name = cur.fetchone()["database_name"]

            print(f"Connected to database: {database_name}")

            # Inspect the current types before attempting the migration.
            cur.execute("""
                SELECT
                    table_name,
                    column_name,
                    data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (
                      (table_name = 'athlete_sessions'
                       AND column_name = 'session_id')
                      OR
                      (table_name = 'tissue_loads_logged'
                       AND column_name = 'session_id')
                  )
                ORDER BY table_name;
            """)

            columns = {
                (row["table_name"], row["column_name"]): row["data_type"]
                for row in cur.fetchall()
            }

            required_columns = {
                ("athlete_sessions", "session_id"),
                ("tissue_loads_logged", "session_id"),
            }

            if not required_columns.issubset(columns):
                raise RuntimeError(
                    "Expected session_id columns were not found in "
                    "the public schema. No migration performed."
                )

            for key, data_type in columns.items():
                if data_type not in ("uuid", "text"):
                    raise RuntimeError(
                        f"Unexpected type for {key[0]}.{key[1]}: "
                        f"{data_type}. Expected UUID or TEXT."
                    )

            if all(data_type == "text" for data_type in columns.values()):
                print("Both session_id columns are already TEXT.")
                conn.commit()
                return

            print("Current column types:")
            for (table, column), data_type in columns.items():
                print(f"  {table}.{column}: {data_type}")

            print("Removing the session foreign key temporarily...")

            cur.execute("""
                ALTER TABLE public.tissue_loads_logged
                DROP CONSTRAINT IF EXISTS
                    tissue_loads_logged_session_id_fkey;
            """)

            print("Converting athlete_sessions.session_id...")

            cur.execute("""
                ALTER TABLE public.athlete_sessions
                ALTER COLUMN session_id TYPE TEXT
                USING session_id::TEXT;
            """)

            print("Converting tissue_loads_logged.session_id...")

            cur.execute("""
                ALTER TABLE public.tissue_loads_logged
                ALTER COLUMN session_id TYPE TEXT
                USING session_id::TEXT;
            """)

            print("Restoring the session foreign key...")

            cur.execute("""
                ALTER TABLE public.tissue_loads_logged
                ADD CONSTRAINT tissue_loads_logged_session_id_fkey
                FOREIGN KEY (session_id)
                REFERENCES public.athlete_sessions(session_id)
                ON DELETE CASCADE;
            """)

        # PostgreSQL DDL is transactional: commit all changes together.
        conn.commit()
        print("Migration completed successfully!")

    except Exception as exc:
        if conn is not None:
            conn.rollback()

        logging.exception("Session ID migration failed")
        print(f"Migration failed: {exc}")
        print("Changes rolled back.")
        raise

    finally:
        if conn is not None:
            conn.close()
            print("Database connection closed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
