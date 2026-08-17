import os
import psycopg
import time
import json
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from collections import defaultdict

RATE_LIMIT_MAX = 5          # max calls
RATE_LIMIT_WINDOW = 60      # per this many seconds
_call_history: dict[str, list[float]] = defaultdict(list)

def check_rate_limit(user_id: str) -> bool:
    """Return True if user is within their rate limit, False if exceeded."""
    now = time.time()
    recent = [t for t in _call_history[user_id] if now - t < RATE_LIMIT_WINDOW]
    _call_history[user_id] = recent
    if len(recent) >= RATE_LIMIT_MAX:
        return False
    _call_history[user_id].append(now)
    return True



load_dotenv()

mcp = MCPServer("fashion-catalog")

def get_conn():
    return psycopg.connect(os.environ["DATABASE_URL"])

def authenticate(api_key: str) -> str | None:
    """Look up a user by API key. Returns user_id if valid, None otherwise."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE api_key = %(key)s", {"key": api_key}
        ).fetchone()
        return row[0] if row else None




def log_action(user_id: str | None, tool: str, args: dict, result: str, latency_ms: int):
    """Record a tool call in the audit log."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO audit_log (user_id, tool, args, result, latency_ms)
            VALUES (%(user_id)s, %(tool)s, %(args)s, %(result)s, %(latency_ms)s)
            """,
            {
                "user_id": user_id,
                "tool": tool,
                "args": json.dumps(args),
                "result": result,
                "latency_ms": latency_ms,
            },
        )
        conn.commit()

@mcp.tool()
def search_garments(
    article_type: str = "",
    colour: str = "",
    max_price_usd: float = 10000,
    limit: int = 10,
) -> list[dict]:
    """Search the garment catalog by type, colour, and max price."""
    query = """
        SELECT id, name, article_type, colour, price_cents, fabric
        FROM garments
        WHERE (%(article_type)s = '' OR article_type ILIKE %(article_type)s)
          AND (%(colour)s = '' OR colour ILIKE %(colour)s)
          AND price_cents <= %(max_cents)s
        LIMIT %(limit)s
    """
    params = {
        "article_type": article_type,
        "colour": colour,
        "max_cents": int(max_price_usd * 100),
        "limit": limit,
    }
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        cols = ["id", "name", "article_type", "colour", "price_cents", "fabric"]
        return [dict(zip(cols, r)) for r in rows]

if __name__ == "__main__":
    mcp.run()

@mcp.tool()
def check_availability(garment_id: int, size: str) -> dict:
    """Check stock quantity for a specific garment and size."""
    query = """
        SELECT g.name, i.size, i.qty
        FROM inventory i
        JOIN garments g ON g.id = i.garment_id
        WHERE i.garment_id = %(garment_id)s AND i.size = %(size)s
    """
    with get_conn() as conn:
        row = conn.execute(query, {"garment_id": garment_id, "size": size}).fetchone()
        if row is None:
            return {"error": f"No record for garment {garment_id} in size {size}"}
        name, size_val, qty = row
        return {
            "garment_id": garment_id,
            "name": name,
            "size": size_val,
            "qty": qty,
            "in_stock": qty > 0,
        }

CARE_MAP = {
    "Cotton": "Machine wash cold, tumble dry low.",
    "Silk": "Dry clean only, or hand wash cold and lay flat to dry.",
    "Linen": "Machine wash cold, air dry to avoid shrinkage.",
    "Polyester": "Machine wash warm, tumble dry low.",
    "Denim": "Wash cold, inside out, tumble dry low.",
    "Wool": "Dry clean recommended, or hand wash cold and lay flat.",
    "Georgette": "Dry clean or gentle hand wash, do not wring.",
}

@mcp.tool()
def get_care_instructions(garment_id: int) -> dict:
    """Get fabric and care instructions for a garment."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name, fabric FROM garments WHERE id = %(id)s", {"id": garment_id}
        ).fetchone()
        if row is None:
            return {"error": f"Garment {garment_id} not found"}
        name, fabric = row
        return {
            "garment_id": garment_id,
            "name": name,
            "fabric": fabric,
            "care": CARE_MAP.get(fabric, "Check garment label for care instructions."),
        }

@mcp.tool()
def save_to_wishlist(api_key: str, garment_id: int) -> dict:
    """Save a garment to the authenticated user's wishlist."""
    start = time.time()
    user_id = authenticate(api_key)

    if user_id is None:
        log_action(None, "save_to_wishlist", {"garment_id": garment_id},
                   "auth_failed", int((time.time() - start) * 1000))
        return {"error": "Invalid API key"}

    if not check_rate_limit(user_id):
        log_action(user_id, "save_to_wishlist", {"garment_id": garment_id},
                   "rate_limited", int((time.time() - start) * 1000))
        return {"error": "Rate limit exceeded. Try again shortly."}

    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM garments WHERE id = %(id)s", {"id": garment_id}
        ).fetchone()
        if not exists:
            log_action(user_id, "save_to_wishlist", {"garment_id": garment_id},
                       "not_found", int((time.time() - start) * 1000))
            return {"error": f"Garment {garment_id} does not exist"}

        conn.execute(
            """
            INSERT INTO wishlist (user_id, garment_id) VALUES (%(user_id)s, %(garment_id)s)
            ON CONFLICT DO NOTHING
            """,
            {"user_id": user_id, "garment_id": garment_id},
        )
        conn.commit()

    latency = int((time.time() - start) * 1000)
    log_action(user_id, "save_to_wishlist", {"garment_id": garment_id}, "success", latency)
    return {"status": "saved", "user_id": user_id, "garment_id": garment_id}