import os
import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

mcp = MCPServer("fashion-catalog")

def get_conn():
    return psycopg.connect(os.environ["DATABASE_URL"])

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