import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
conn = psycopg.connect(os.environ["DATABASE_URL"])

rows = conn.execute("""
    SELECT user_id, tool, args, result, latency_ms, at
    FROM audit_log
    ORDER BY at DESC
    LIMIT 5
""").fetchall()

for r in rows:
    print(r)