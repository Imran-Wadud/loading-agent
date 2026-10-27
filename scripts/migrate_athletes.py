# migrate_athletes.py

import os
import sys
import logging
from dotenv import load_dotenv
from pathlib import Path

# Load .env from the project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor


# Add project root directory to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


def get_db_connection():
    """Returns a PostgreSQL/Zetaris connection with dictionary cursor support."""
    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT", "5432"))
    dbname = os.getenv("POSTGRES_DB") or os.getenv("DB_NAME", "postgres")
    user = os.getenv("POSTGRES_USER") or os.getenv("DB_USER", "postgres")
    password = (
        os.getenv("POSTGRES_PASSWORD")
        or os.getenv("DB_PASSWORD", "postgres")
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
    print("Connecting to database...")
    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            print("Creating `athletes` table if it does not exist...")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS athletes (
                    athlete_id VARCHAR(100) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    experience_level VARCHAR(50),
                    primary_goal VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    capacity_scalar NUMERIC(4, 2) DEFAULT 1.00,
                    rpe_bias NUMERIC(4, 2) DEFAULT 0.00,
                    injury_notes TEXT
                );
            """)

            print("Adding missing columns to existing `athletes` table...")

            cur.execute("""
                ALTER TABLE athletes
                    ADD COLUMN IF NOT EXISTS capacity_scalar NUMERIC(4, 2)
                        DEFAULT 1.00,
                    ADD COLUMN IF NOT EXISTS rpe_bias NUMERIC(4, 2)
                        DEFAULT 0.00,
                    ADD COLUMN IF NOT EXISTS injury_notes TEXT;
            """)

            print("Backfilling NULL values for athlete scaling fields...")

            cur.execute("""
                UPDATE athletes
                SET
                    capacity_scalar = COALESCE(capacity_scalar, 1.00),
                    rpe_bias = COALESCE(rpe_bias, 0.00)
                WHERE
                    capacity_scalar IS NULL
                    OR rpe_bias IS NULL;
            """)

            conn.commit()
            print("Migration completed successfully!")

    except Exception as e:
        conn.rollback()
        logging.exception("Athletes migration failed")
        print(f"Migration failed: {e}")
        raise

    finally:
        conn.close()
        print("Database connection closed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()