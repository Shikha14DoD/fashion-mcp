import os
import hashlib
import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

FABRICS = ["Cotton", "Silk", "Linen", "Polyester", "Denim", "Wool", "Georgette"]
SIZES = ["XS", "S", "M", "L", "XL"]

def seeded(item_id, salt):
    h = hashlib.md5(f"{item_id}{salt}".encode()).hexdigest()
    return int(h[:8], 16)

df = pd.read_csv("data/styles.csv", on_bad_lines="skip")
df = df.dropna(subset=["productDisplayName"])

garment_rows = []
inventory_rows = []

for r in df.itertuples():
    price_cents = 1500 + seeded(r.id, "p") % 28500
    fabric = FABRICS[seeded(r.id, "f") % len(FABRICS)]
    garment_rows.append((
        r.id, r.productDisplayName, r.gender, r.masterCategory,
        r.articleType, r.baseColour, r.season, r.usage, price_cents, fabric
    ))
    for size in SIZES:
        qty = seeded(r.id, size) % 6
        inventory_rows.append((r.id, size, qty))

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO garments VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            garment_rows,
        )
        cur.executemany(
            "INSERT INTO inventory VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            inventory_rows,
        )
        cur.execute(
            "INSERT INTO users VALUES ('demo_user','demo_key_123') ON CONFLICT DO NOTHING"
        )
    conn.commit()

print(f"loaded {len(garment_rows)} garments, {len(inventory_rows)} inventory rows")