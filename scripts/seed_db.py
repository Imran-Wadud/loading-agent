# scripts/seed_db.py
import sys
import logging
from pathlib import Path

# Add project root directory to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.adapters.zetaris import get_db_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_db")

DEFAULT_ATHLETES = [
    ("athlete_001", "Default Athlete", "INTERMEDIATE")
]

BODY_PARTS_DICTIONARY = [
    # TENDONS & CONNECTIVE TISSUE
    ("Patellar Tendon", "TENDON"),
    ("Achilles Tendon", "TENDON"),
    ("Rotator Cuff Tendons", "TENDON"),
    ("Distal Biceps Tendon", "TENDON"),
    ("Triceps Brachii Tendon", "TENDON"),
    ("Hamstring Tendons", "TENDON"),
    ("Plantar Fascia", "TENDON"),
    ("Iliotibial (IT) Band", "TENDON"),
    ("Common Extensor Tendon (Lateral Epicondyle)", "TENDON"),
    ("Common Flexor Tendon (Medial Epicondyle)", "TENDON"),

    # LOWER EXTREMITY MUSCLES
    ("Quadriceps Femoris", "MUSCLE"),
    ("Hamstrings (Biceps Femoris / Semitendinosus / Semimembranosus)", "MUSCLE"),
    ("Gastrocnemius & Soleus", "MUSCLE"),
    ("Tibialis Anterior", "MUSCLE"),
    ("Gluteus Maximus", "MUSCLE"),
    ("Gluteus Medius & Minimus", "MUSCLE"),
    ("Hip Adductors (Adductor Longus / Magnus / Brevis)", "MUSCLE"),
    ("Iliopsoas (Hip Flexors)", "MUSCLE"),

    # UPPER EXTREMITY & SHOULDER MUSCLES
    ("Pectoralis Major & Minor", "MUSCLE"),
    ("Latissimus Dorsi", "MUSCLE"),
    ("Trapezius (Upper / Middle / Lower)", "MUSCLE"),
    ("Rhomboids", "MUSCLE"),
    ("Deltoids (Anterior / Lateral / Posterior)", "MUSCLE"),
    ("Biceps Brachii & Brachialis", "MUSCLE"),
    ("Triceps Brachii", "MUSCLE"),
    ("Wrist Flexors & Extensors (Forearms)", "MUSCLE"),

    # CORE & AXIAL MUSCLES
    ("Rectus Abdominis", "MUSCLE"),
    ("Obliques (Internal / External)", "MUSCLE"),
    ("Erector Spinae", "MUSCLE"),
    ("Quadratus Lumborum", "MUSCLE")
]


def seed_database():
    schema_file = root_dir / "data" / "schema.sql"
    if not schema_file.exists():
        logger.error(f"Schema DDL file missing at {schema_file}")
        return

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Apply DDL Schema
            logger.info("Applying database table definitions from data/schema.sql...")
            cur.execute(schema_file.read_text(encoding="utf-8"))

            # 2. Seed Default Athletes
            logger.info("Seeding default athlete profiles...")
            cur.executemany(
                """
                INSERT INTO athletes (athlete_id, name, experience_level)
                VALUES (%s, %s, %s)
                ON CONFLICT (athlete_id) DO UPDATE 
                SET name = EXCLUDED.name,
                    experience_level = EXCLUDED.experience_level;
                """,
                DEFAULT_ATHLETES
            )

            # 3. Seed Body Parts Dictionary
            logger.info(f"Seeding {len(BODY_PARTS_DICTIONARY)} canonical body parts...")
            cur.executemany(
                """
                INSERT INTO body_parts (name, tissue_type)
                VALUES (%s, %s)
                ON CONFLICT (name) DO UPDATE 
                SET tissue_type = EXCLUDED.tissue_type;
                """,
                BODY_PARTS_DICTIONARY
            )

        conn.commit()
        logger.info("Database schema and seed data applied successfully.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Database seeding failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    seed_database()