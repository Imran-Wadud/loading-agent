# clean.py
from app.adapters.zetaris import get_db_connection

conn = get_db_connection()
try:
    with conn.cursor() as cur:
        # Delete tissue loads linked to athlete_001 first to prevent Foreign Key conflicts
        cur.execute("""
            DELETE FROM tissue_loads_logged 
            WHERE session_id IN (
                SELECT session_id FROM athlete_sessions WHERE athlete_id = 'athlete_001'
            );
        """)
        
        # Delete sessions for athlete_001
        cur.execute("DELETE FROM athlete_sessions WHERE athlete_id = 'athlete_001';")
        
        conn.commit()
        print("✅ Successfully cleared all test session history for athlete_001!")
finally:
    conn.close()