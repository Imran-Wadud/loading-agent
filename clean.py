# clean.py
from app.agents.extractor import get_db_connection

conn = get_db_connection()
try:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM athlete_sessions WHERE athlete_id = 'athlete_001';")
        conn.commit()
        print("Successfully cleared athlete_001 test history!")
finally:
    conn.close()