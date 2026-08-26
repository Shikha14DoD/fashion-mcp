
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
- [x] Execute the tool call Gemini requests and feed results back
- [ ] Multi-turn conversation loop
- [ ] LangGraph state machine with human-in-the-loop confirmation gate
- [ ] Simple chat UI
- [ ] Evaluation harness (task success rate, groundedness)
- [ ] Public deployment + demo GIF


- [x] Rate limiting on write actions

Note: Rate limiting is in-memory (5 calls/60s per user) — resets on
server restart and wouldn't hold up across multiple server instances. A
production version would use Redis for shared state.

**Status: Stage 3 in progress** — full loop working: Gemini selects a tool,
the agent executes it via the real MCP protocol, and results return
successfully. Retry with exponential backoff added after hitting a real
Gemini 503 during development. Multi-turn conversation and LangGraph state
machine not yet built.


**Transient API failures:** hit a real `503 UNAVAILABLE` from Gemini's free
tier during development — not a bug, just temporary overload. Added retry
with exponential backoff (1s, 2s, 4s) rather than manually retrying, since
this is a realistic failure mode any production agent needs to handle
gracefully rather than crash on.

**Hallucinated capability (caught via testing):** without explicit scope
constraints, the agent — when asked to "place the order" — fabricated a full
checkout flow: an order summary, a request for shipping/payment details, and
two different fake checkout URLs, despite no purchasing tool existing
anywhere in the system. This is a serious finding for any LLM-backed product:
an agent can convincingly simulate capabilities it doesn't have. Fixed with
an explicit system prompt enumerating the exact 4 available tools and
instructing the model to state its real limitations rather than invent a
plausible-sounding flow. Verified fixed: the same request now correctly
responds that no checkout capability exists. This is now a candidate test
case for the eval harness — "does the agent stay within its real tool
boundaries" is a measurable, repeatable check worth automating.

**Multi-provider resilience:** added Groq (openai/gpt-oss-120b) as an
automatic fallback when Gemini's free-tier daily quota (20 requests/day) is
exhausted or the service returns repeated 503s. The fallback required
translating between two different function-calling schemas (Gemini's
`types.Tool` format vs. Groq's OpenAI-compatible `tools=[...]` format) and
normalizing both providers' responses into one consistent shape before the
rest of the graph consumes them — the graph itself doesn't know or care
which provider actually answered.

**Fabricated tool-output shape (caught via testing):** when summarizing a
`check_availability` result, the model displayed a JSON block with fields
`available` and `stock_quantity` — but the tool actually returns `in_stock`
and `qty`. The final English sentence happened to be correct, but the
"raw output" shown to the user was fabricated to look plausible rather than
copied from the real result. This is a subtler version of the earlier
hallucinated-checkout bug: even with correct final answers, a model can
invent supporting detail that looks authoritative but isn't real. Flagged as
a concrete test case for the eval harness — comparing displayed tool output
against the actual tool schema is a measurable, automatable check.

**Tool-choice inconsistency across calls (caught via web backend testing):**
the streaming "final answer" call to Groq omitted the tools list entirely,
assuming that meant "no tool use." In practice, the model independently
decided to attempt a tool call anyway on that separate request, and Groq's
API rejected the response outright with `Tool choice is none, but model
called a tool`. Root cause: two separate calls to the same provider, each
with a different, implicit view of whether tools were available, with no
explicit agreement between them. Fixed by passing `tools=groq_tools` together
with `tool_choice="none"` on the streaming call — explicitly stating "these
tools exist, but you may not use them right now" rather than omitting them
and hoping. This bug only surfaced under the FastAPI backend, not the
terminal version, because the web request that triggered it lacked the
multi-turn context that normally keeps the model from reconsidering a tool
call mid-stream — a good example of how a system that appears correct in one
interface can fail under different real-world usage patterns.

**Same Groq error resurfacing in production (caught on first live Render
deploy):** even after the `tool_choice="none"` fix above, the identical
`Tool choice is none, but model called a tool` error showed up again once the
app was deployed — this time raised as `groq.APIError` from inside the
streaming response parser itself. Turns out `tool_choice="none"` tells the
API what's allowed, but doesn't stop the underlying model
(`openai/gpt-oss-120b`) from occasionally emitting a tool-call-shaped output
anyway; Groq's client detects the mismatch mid-stream and raises rather than
silently dropping it. The request survived this first time only because
LangGraph retries a failed node once automatically, and the retry happened
not to trigger the same behavior — not something to depend on. Fixed by
wrapping the streaming call in a `try/except` on `groq.APIError` and
returning a plain apologetic message on failure, so a model-level quirk
degrades to a normal chat reply instead of a 500 the frontend sees as a
network failure. Also caught a related mistake while adding this: the file
already imported a Groq error type aliased as `GroqAPIError`, but it pointed
at `APIStatusError`, a subclass of the actual exception being raised
(`APIError`) — so the obvious `except GroqAPIError` would silently have
missed it. Worth remembering: an unused defensive import can be as wrong as
having no import at all if nothing ever exercises it.

**LLM asking the user for a secret it was never meant to have (caught on the
first real confirmation-gate test):** `save_to_wishlist(api_key, garment_id)`
correctly requires `api_key` in its MCP schema — a direct MCP client has no
other way to authenticate. But that same schema was handed straight to
Gemini and Groq as the tool's definition, so the model saw `api_key` as a
required argument it had no value for and asked the user to provide one,
instead of calling the tool. The result: the human-confirmation gate — the
one feature this whole project is built around — had never actually fired in
the deployed app, because the write tool never got far enough to trigger it.
Fixed by stripping `api_key` out of the schema shown to the LLM (`strip_hidden_args`
in `graph_nodes.py`) before it's turned into Gemini/Groq tool definitions,
while `tools_node` keeps injecting the real, session-bound key server-side
exactly as it always did. The MCP tool's actual schema is untouched, so
calling it directly (MCP Inspector, etc.) still correctly demands a key.

**Streaming helpers crashing on ordinary model output (caught seconds after
the fix above, testing the confirmation gate for the first time):**
`stream_gemini_text` and `stream_groq_text` both had a `print(delta, end="",
flush=True)` left over from before `web_server.py` existed, when this agent
only ran in a terminal. On Windows, stdout defaults to `cp1252`, which can't
encode a fair amount of ordinary Unicode — in this case a narrow no-break
space the model put before a dollar amount. That raised a plain
`UnicodeEncodeError`, not a `GroqAPIError`, so it went straight past the
error handling added above and 500'd the request outright. Fixed by deleting
both print calls: `web_server.py` only ever reads the function's return
value, never stdout, so they'd been dead weight since the web backend was
added — and dead weight that could crash a request.