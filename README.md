
Imagine a personal stylist you can text: *"I need something for an outdoor
September wedding, under $200, and I already own gold jewellery."* Instead of
scrolling through filters, you just describe what you need, and the assistant
figures out the rest.

Behind that simple chat is a real system with three parts:

1. The catalog — a database of 44,000+ real garments (names, categories,
colours, fabric, simulated prices and stock), sitting in a cloud Postgres
database.

2. The toolbox (MCP server)— a set of well-defined actions the assistant
is allowed to take: search the catalog, check if an item is in stock, look up
how to care for a fabric, or save an item to a wishlist. Each action is
authenticated (only a logged-in user can save to *their* wishlist) and every
attempt — successful or not — is recorded in an audit log, the same way a
real production system tracks who did what.

3. The agent (in progress) — the "brain" that takes your plain-English
request, decides which tools to call and in what order, and asks for your
confirmation before doing anything permanent, like saving an item.

### How a request flows through the system, end to end

1. You type a request in plain English.
2. The agent breaks it into filters (occasion, budget, colour to avoid, etc.)
3. It calls the `search_garments` tool with those filters.
4. It calls `check_availability` on the results to remove out-of-stock items.
5. It presents a few real options, with real prices and fabrics — never
   invented ones.
6. If you ask to save an item, the agent pauses and asks you to confirm
   before it calls `save_to_wishlist`.
7. That action, and everything before it, is logged — so there's always a
   record of what the assistant did and why.

The interesting engineering isn't the styling advice — it's steps 3–7: an
assistant that can only take actions it's explicitly allowed to take, always
knows who it's acting on behalf of, and never edits anything without asking
first.


# Fashion MCP — Authenticated Styling Agent

An MCP server exposing typed, authenticated tools over a real fashion product
catalog (44k+ garments), with audit logging on every call. A LangGraph agent
(in progress) sits on top, handling multi-turn styling requests with a
human-confirmation gate before any write action.

**Status: Stage 3 in progress** — MCP server complete with 4 tools, auth,
audit logging, and rate limiting. Agent layer: MCP client connection working,
Gemini function-calling verified. Tool execution loop and LangGraph state
machine not yet built.

## Why this exists

Built to demonstrate production-shaped agent infrastructure: authentication,
per-user scoping, and an auditable action log — not just an LLM wrapper.

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

Every call to `save_to_wishlist` is logged to `audit_log` — including failed
auth attempts — with user id, arguments, result, and latency.


## Engineering notes

**Tool registration ordering bug (Stage 3):** `mcp.run()` was accidentally
left in the middle of `server.py`, after only the first tool was defined.
Since Python executes top to bottom, this silently started the server with
only 1 of 4 tools registered — no error, just missing functionality. Caught
by testing the server in isolation from the client and comparing tool counts
at each layer. Fix: moved `if __name__ == "__main__": mcp.run()` to the true
end of the file, after all `@mcp.tool()` definitions.

**LLM-to-database value mismatch:** Gemini's function-calling naturally
produces values like `"t-shirt"` when asked for casual clothing, while the
catalog stores `"Tshirts"` (no space or hyphen). An exact or case-insensitive
match alone missed this. Fixed with two layers: (1) the tool's docstring now
shows Gemini real example values from the catalog, reducing how often this
happens, and (2) the SQL query normalizes both the stored value and the
incoming argument (lowercase, strip spaces/hyphens) before comparing, so the
match succeeds even when phrasing differs. This is a documented example of
why LLM-generated arguments can't be trusted to match a real schema exactly,
and why validation/normalization has to happen in code, not just in the
prompt.

**Known limitation:** the normalization above transforms `article_type` on
every query rather than at write time, so a standard database index can't be
used on it. Fine at the current scale (44k rows) but would need an expression
index or a precomputed normalized column at larger scale.

## Running locally

\`\`\`bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install "psycopg[binary]" pandas python-dotenv pydantic "mcp[cli]"

# .env file with DATABASE_URL=your_neon_connection_string

python load_data.py     # one-time: load catalog into Postgres
mcp dev server.py       # launch MCP Inspector to test tools
\`\`\`

## Roadmap

## Roadmap

- [x] `get_care_instructions` tool
- [x] Rate limiting on write actions
- [x] Real MCP client-server connection (not a direct import)
- [x] Gemini function-calling: LLM selects the correct tool and arguments
- [ ] Execute the tool call Gemini requests and feed results back
- [ ] Multi-turn conversation loop
- [ ] LangGraph state machine with human-in-the-loop confirmation gate
- [ ] Simple chat UI
- [ ] Evaluation harness (task success rate, groundedness)
- [ ] Public deployment + demo GIF


- [x] Rate limiting on write actions

Note: Rate limiting is in-memory (5 calls/60s per user) — resets on
server restart and wouldn't hold up across multiple server instances. A
production version would use Redis for shared state.