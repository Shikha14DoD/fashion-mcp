# Fashion MCP - Authenticated Styling Agent

**Live demo:** [fashion-mcp-frontend.onrender.com](https://fashion-mcp-frontend.onrender.com)
**Backend API:** [fashion-mcp-backend.onrender.com](https://fashion-mcp-backend.onrender.com)

Both are on Render's free tier and spin down after ~15 minutes idle, so the
first request after a lull can take 20-30s to wake up - that's a cold
start, not a bug.

Imagine a personal stylist you can text: *"I need something for an outdoor
September wedding, under $200, and I already own gold jewellery."* Instead of
scrolling through filters, you just describe what you need, and the assistant
figures out the rest.

Behind that simple chat is a real system with three parts: a **catalog**
(44,000+ real garments in a cloud Postgres database), a **toolbox** (an MCP
server exposing authenticated, audit-logged actions - search, check stock,
save to a wishlist), and an **agent** that decides which tools to call and
pauses for your confirmation before doing anything permanent.

The interesting engineering isn't the styling advice, it's that part: the
agent can only take actions it's explicitly allowed to take, always knows
who it's acting on behalf of, and never writes anything without asking
first - and every attempt, successful or not, ends up in an audit log.

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
chronological write-up of each one, what broke, how it was diagnosed, and
what actually fixed it.

## Evaluation harness

`eval_harness.py` drives the agent through its real HTTP API and checks
each response against ground truth pulled directly from the database - not
just that the agent replies, but that the reply is *true*. See
[ENGINEERING_NOTES.md](ENGINEERING_NOTES.md#evaluation-harness-details) for
exactly what it checks and why.

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

Known limitations, including in-memory rate limiting, are noted in
[ENGINEERING_NOTES.md](ENGINEERING_NOTES.md).
