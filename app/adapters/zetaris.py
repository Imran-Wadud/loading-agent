import os
import uuid
import requests
from typing import List, Dict, Any

def run_zetaris_sql(sql_query: str) -> List[Dict[str, Any]]:
    """
    Executes a SQL query against the local Zetaris engine over REST.
    Returns a list of record dictionaries.
    """
    rest_url = os.getenv("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0").rstrip("/")
    url = f"{rest_url}/sql-editor/sqls/run"
    
    headers = {
        "Authorization": f"Bearer {os.getenv('ZETARIS_API_KEY', 'default_key')}",
        "X-Org-ID": os.getenv("ZETARIS_ORG_ID", "1"),
        "X-Request-ID": str(uuid.uuid4()),
        "Content-Type": "application/json"
    }
    
    payload = {
        "queryId": str(uuid.uuid4()),
        "sql": sql_query,
        "source": "SqlEditor",
        "limit": 100
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except Exception as e:
        print(f"[Zetaris REST Error] Failed to execute query: {e}")
        return []