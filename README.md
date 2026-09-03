**Live demo:** [fashion-mcp-frontend.onrender.com](https://fashion-mcp-frontend.onrender.com)
**Backend API:** [fashion-mcp-backend.onrender.com](https://fashion-mcp-backend.onrender.com)

Imagine a personal stylist you can text: *"I need something for an outdoor
September wedding, under $200, and I already own gold jewellery."* Instead of
scrolling through filters, you just describe what you need, and the assistant
figures out the rest.

Behind that simple chat is a real system with three parts:

1. The catalog - a database of 44,000+ real garments (names, categories,
colours, fabric, simulated prices and stock), sitting in a cloud Postgres
database.

2. The toolbox (MCP server) - a set of well-defined actions the assistant
is allowed to take: search the catalog, check if an item is in stock, look up
how to care for a fabric, or save an item to a wishlist. Each action is
authenticated (only a logged-in user can save to *their* wishlist) and every
attempt - successful or not - is recorded in an audit log, the same way a
real production system tracks who did what.

3. The agent - the "brain" that takes your plain-English request, decides
which tools to call and in what order, and asks for your confirmation before
doing anything permanent, like saving an item.

### How a request flows through the system, end to end

1. You type a request in plain English.
2. The agent breaks it into filters (occasion, budget, colour to avoid, etc.)
3. It calls the `search_garments` tool with those filters.
4. It calls `check_availability` on the results to remove out-of-stock items.
5. It presents a few real options, with real prices and fabrics - never
   invented ones.
6. If you ask to save an item, the agent pauses and asks you to confirm
   before it calls `save_to_wishlist`.
7. That action, and everything before it, is logged - so there's always a
   record of what the assistant did and why.

The interesting engineering isn't the styling advice - it's steps 3-7: an
assistant that can only take actions it's explicitly allowed to take, always
knows who it's acting on behalf of, and never edits anything without asking
first.


# Fashion MCP - Authenticated Styling Agent

The live demo and backend (linked at the top) are both on Render's free
tier and spin down after ~15 minutes idle, so the first request after a
lull can take 20-30s to wake up - that's a cold start, not a bug.

An MCP server exposing typed, authenticated tools over a real fashion product
catalog (44k+ garments), with audit logging on every call. A LangGraph agent
sits on top, handling multi-turn styling requests with a human-confirmation
gate before any write action - wrapped in a FastAPI backend and a browser
chat UI, deployed live on Render.

**Status: Deployed** - MCP server complete with 4 tools, auth, audit
logging, and rate limiting. Agent layer: multi-turn LangGraph state machine
with Groq as the primary LLM and Gemini as fallback, and a human-confirmation
gate on write actions, served over a FastAPI backend with a browser chat
frontend. Remaining: a demo GIF.

## Why this exists

Built to demonstrate production-shaped agent infrastructure: authentication,
per-user scoping, and an auditable action log - not just an LLM wrapper.

## Stack

- Python, MCP Python SDK
- PostgreSQL (Neon), `psycopg`
- Data: Kaggle Fashion Product Images dataset (44,417 garments)
- Pricing and stock are deterministically simulated (seeded from item ID),
  clearly noted since the source dataset has no real price/inventory data

## Tools implemented so far

| Tool | Description | Auth required |
|---|---|---|
| `search_garments` | Filter catalog by type, colour, price | No |
| `check_availability` | Stock check by garment + size | No |
| `save_to_wishlist` | Add item to a user's wishlist | Yes (API key) |

Every call to `save_to_wishlist` is logged to `audit_log` - including failed
auth attempts - with user id, arguments, result, and latency.

## Engineering notes

This project is documented not just as a working demo but as a running log
of the real bugs found and fixed while building and deploying it - a tool
registration ordering bug, LLM/database value mismatches, a Gemini hang
that defeated the multi-provider fallback, an MCP subprocess that silently
lost its database credentials on deploy, and a dozen more. See
**[ENGINEERING_NOTES.md](ENGINEERING_NOTES.md)** for the full,
chronological write-up of each one: what broke, how it was diagnosed, and
what actually fixed it.

Two of the more instructive ones:

**Hallucinated capability (caught via testing):** without explicit scope
constraints, the agent - when asked to "place the order" - fabricated a full
checkout flow: an order summary, a request for shipping/payment details, and
two different fake checkout URLs, despite no purchasing tool existing
anywhere in the system. This is a serious finding for any LLM-backed product:
an agent can convincingly simulate capabilities it doesn't have. Fixed with
an explicit system prompt enumerating the exact 4 available tools and
instructing the model to state its real limitations rather than invent a
plausible-sounding flow. Verified fixed: the same request now correctly
responds that no checkout capability exists.

**The MCP subprocess couldn't see `DATABASE_URL` at all on Render - a real
bug hiding underneath a session's worth of provider debugging (diagnosed
from a user's hypothesis, confirmed with a temporary tool-level diagnostic
endpoint):** `web_server.py` spawns `server.py` via `StdioServerParameters`
with no explicit `env`. The MCP SDK's `stdio_client` does not inherit the
parent process's environment by default - it passes only a small safe-list
(`PATH`, `HOME`, etc. on Linux) merged with whatever `env` is explicitly
given, which was nothing here. Locally this was invisible because
`server.py`'s own `load_dotenv()` call finds the local `.env` file
regardless of what the parent passed down; on Render, `.env` is gitignored
and never deployed, so the subprocess had no way to see `DATABASE_URL` at
all. Confirmed directly: a temporary debug route that called
`search_garments` through the live subprocess, bypassing the LLM entirely,
returned a plain `KeyError: 'DATABASE_URL'`, not a provider issue. Fixed by
passing `env={"DATABASE_URL": os.environ["DATABASE_URL"]}` explicitly to
`StdioServerParameters`.

## Evaluation harness

`eval_harness.py` drives the agent through its real HTTP API
(`/session`, `/chat`, `/confirm`) - the same interface a browser client
uses - and checks each response against ground truth pulled directly from
the same database via the MCP tool functions. It isn't just checking that
the agent replies; it's checking the reply is *true*: a search response
must contain a real price from the catalog, an availability answer must
match the real stock quantity, care instructions must overlap with the
real `CARE_MAP` entry, and the wishlist confirmation gate must fire with
the correct args and no leaked `api_key` - several of these are the exact
failure modes documented in the engineering notes (hallucinated checkout,
fabricated tool output, the api_key leak), turned into repeatable checks
instead of things a human has to happen to notice.

```bash
python eval_harness.py --base-url http://127.0.0.1:8000
# or against the live deploy:
python eval_harness.py --base-url https://fashion-mcp-backend.onrender.com
```

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install "psycopg[binary]" pandas python-dotenv pydantic "mcp[cli]"

# .env file with DATABASE_URL=your_neon_connection_string

python load_data.py     # one-time: load catalog into Postgres
mcp dev server.py       # launch MCP Inspector to test tools
```

## Roadmap

- [x] `get_care_instructions` tool
- [x] Rate limiting on write actions
- [x] Real MCP client-server connection (not a direct import)
- [x] Gemini function-calling: LLM selects the correct tool and arguments
- [x] Execute the tool call Gemini requests and feed results back
- [x] Multi-turn conversation loop
- [x] LangGraph state machine with human-in-the-loop confirmation gate
- [x] Simple chat UI
- [x] Public deployment (Render)
- [x] Evaluation harness (task success rate, groundedness)
- [ ] Demo GIF

Note: rate limiting is in-memory (5 calls/60s per user) - resets on server
restart and wouldn't hold up across multiple server instances. A production
version would use Redis for shared state.
