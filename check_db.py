import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
conn = psycopg.connect(os.environ["DATABASE_URL"])
rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()

import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
conn = psycopg.connect(os.environ["DATABASE_URL"])

rows = conn.execute("""
    SELECT article_type, COUNT(*), ROUND(AVG(price_cents)/100.0, 2) AS avg_usd
    FROM garments
    GROUP BY article_type
    ORDER BY COUNT(*) DESC
    LIMIT 10
""").fetchall()

for r in rows:
    print(r)