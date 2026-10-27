# migrate_tissue_kinetics.py

import sys
import logging
from pathlib import Path

# Add project root directory to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.adapters.zetaris import get_db_connection

def run_migration():
    print("Connecting to database...")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            print("Creating `tissue_kinetics` table...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tissue_kinetics (
                    tissue_type VARCHAR(50) PRIMARY KEY,
                    base_recovery_window_hours NUMERIC(5, 1) NOT NULL,
                    disuse_threshold_days INT NOT NULL,
                    decay_multiplier NUMERIC(4, 2) NOT NULL
                );
            """)
            
            print("Seeding universal tissue constants...")
            cur.execute("""
                INSERT INTO tissue_kinetics (tissue_type, base_recovery_window_hours, disuse_threshold_days, decay_multiplier)
                VALUES 
                    ('MUSCLE', 48.0, 7, 0.25),
                    ('TENDON', 72.0, 4, 0.15)
                ON CONFLICT (tissue_type) DO NOTHING;
            """)
            
            conn.commit()
            print("Migration completed successfully!")
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_migration()