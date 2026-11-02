"""Where LangGraph keeps conversation state between requests."""

import os
import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


async def postgres_checkpointer() -> AsyncPostgresSaver:
    """Conversation state in Postgres, so a restart or Render spin-down doesn't
    wipe in-progress chats (including a pending confirmation)."""
    pool = AsyncConnectionPool(
        conninfo=os.environ["DATABASE_URL"], min_size=1, max_size=5, open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,  # Neon drops idle connections
    )
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver


async def make_checkpointer():
    if sys.platform == "win32":
        # psycopg's async mode can't run on Windows' default Proactor event loop,
        # and the MCP stdio subprocess requires that loop - so local Windows dev
        # keeps state in memory. Render runs Linux and uses Postgres.
        print("[checkpointer] Windows: using in-memory conversation state")
        return InMemorySaver()
    return await postgres_checkpointer()
