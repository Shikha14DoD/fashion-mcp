# Engineering Notes

A chronological log of real bugs found and fixed while building, testing,
and deploying this project - what broke, how it was diagnosed, and what
actually fixed it. Moved out of the main [README](README.md) to keep that
one scannable; this is the detailed version for anyone who wants it.

**Tool registration ordering bug (Stage 3):** `mcp.run()` was accidentally
left in the middle of `server.py`, after only the first tool was defined.
Since Python executes top to bottom, this silently started the server with
only 1 of 4 tools registered - no error, just missing functionality. Caught
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

**Transient API failures:** hit a real `503 UNAVAILABLE` from Gemini's free
tier during development - not a bug, just temporary overload. Added retry
with exponential backoff (1s, 2s, 4s) rather than manually retrying, since
this is a realistic failure mode any production agent needs to handle
gracefully rather than crash on.

**Hallucinated capability (caught via testing):** without explicit scope
constraints, the agent - when asked to "place the order" - fabricated a full
checkout flow: an order summary, a request for shipping/payment details, and
two different fake checkout URLs, despite no purchasing tool existing
anywhere in the system. This is a serious finding for any LLM-backed product:
an agent can convincingly simulate capabilities it doesn't have. Fixed with
an explicit system prompt enumerating the exact 4 available tools and
instructing the model to state its real limitations rather than invent a
plausible-sounding flow. Verified fixed: the same request now correctly
responds that no checkout capability exists. This is now a candidate test
case for the eval harness - "does the agent stay within its real tool
boundaries" is a measurable, repeatable check worth automating.

**Multi-provider resilience:** added Groq (openai/gpt-oss-120b) as an
automatic fallback when Gemini's free-tier daily quota (20 requests/day) is
exhausted or the service returns repeated 503s. The fallback required
translating between two different function-calling schemas (Gemini's
`types.Tool` format vs. Groq's OpenAI-compatible `tools=[...]` format) and
normalizing both providers' responses into one consistent shape before the
rest of the graph consumes them - the graph itself doesn't know or care
which provider actually answered.

**Fabricated tool-output shape (caught via testing):** when summarizing a
`check_availability` result, the model displayed a JSON block with fields
`available` and `stock_quantity` - but the tool actually returns `in_stock`
and `qty`. The final English sentence happened to be correct, but the
"raw output" shown to the user was fabricated to look plausible rather than
copied from the real result. This is a subtler version of the earlier
hallucinated-checkout bug: even with correct final answers, a model can
invent supporting detail that looks authoritative but isn't real. Flagged as
a concrete test case for the eval harness - comparing displayed tool output
against the actual tool schema is a measurable, automatable check.

**Tool-choice inconsistency across calls (caught via web backend testing):**
the streaming "final answer" call to Groq omitted the tools list entirely,
assuming that meant "no tool use." In practice, the model independently
decided to attempt a tool call anyway on that separate request, and Groq's
API rejected the response outright with `Tool choice is none, but model
called a tool`. Root cause: two separate calls to the same provider, each
with a different, implicit view of whether tools were available, with no
explicit agreement between them. Fixed by passing `tools=groq_tools` together
with `tool_choice="none"` on the streaming call - explicitly stating "these
tools exist, but you may not use them right now" rather than omitting them
and hoping. This bug only surfaced under the FastAPI backend, not the
terminal version, because the web request that triggered it lacked the
multi-turn context that normally keeps the model from reconsidering a tool
call mid-stream - a good example of how a system that appears correct in one
interface can fail under different real-world usage patterns.

**Same Groq error resurfacing in production (caught on first live Render
deploy):** even after the `tool_choice="none"` fix above, the identical
`Tool choice is none, but model called a tool` error showed up again once the
app was deployed - this time raised as `groq.APIError` from inside the
streaming response parser itself. Turns out `tool_choice="none"` tells the
API what's allowed, but doesn't stop the underlying model
(`openai/gpt-oss-120b`) from occasionally emitting a tool-call-shaped output
anyway; Groq's client detects the mismatch mid-stream and raises rather than
silently dropping it. The request survived this first time only because
LangGraph retries a failed node once automatically, and the retry happened
not to trigger the same behavior - not something to depend on. Fixed by
wrapping the streaming call in a `try/except` on `groq.APIError` and
returning a plain apologetic message on failure, so a model-level quirk
degrades to a normal chat reply instead of a 500 the frontend sees as a
network failure. Also caught a related mistake while adding this: the file
already imported a Groq error type aliased as `GroqAPIError`, but it pointed
at `APIStatusError`, a subclass of the actual exception being raised
(`APIError`) - so the obvious `except GroqAPIError` would silently have
missed it. Worth remembering: an unused defensive import can be as wrong as
having no import at all if nothing ever exercises it.

**LLM asking the user for a secret it was never meant to have (caught on the
first real confirmation-gate test):** `save_to_wishlist(api_key, garment_id)`
correctly requires `api_key` in its MCP schema - a direct MCP client has no
other way to authenticate. But that same schema was handed straight to
Gemini and Groq as the tool's definition, so the model saw `api_key` as a
required argument it had no value for and asked the user to provide one,
instead of calling the tool. The result: the human-confirmation gate - the
one feature this whole project is built around - had never actually fired in
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
encode a fair amount of ordinary Unicode - in this case a narrow no-break
space the model put before a dollar amount. That raised a plain
`UnicodeEncodeError`, not a `GroqAPIError`, so it went straight past the
error handling added above and 500'd the request outright. Fixed by deleting
both print calls: `web_server.py` only ever reads the function's return
value, never stdout, so they'd been dead weight since the web backend was
added - and dead weight that could crash a request.

**Groq fallback reliability under real quota pressure (caught testing the
live deploy):** once Gemini's free-tier daily quota (20 requests/day) ran out
partway through testing, every turn fell back to Groq's `openai/gpt-oss-120b`
for both the tool-selection step and the final-answer step. That model
occasionally skipped calling `search_garments` entirely and returned a vague
"technical issue accessing the catalog" reply instead - confirmed to not be a
real error, since calling `search_garments` directly against the same
database returned results immediately. Not something request parameters can
fix: it's a genuine reliability gap between the two providers, and the kind
of thing an eval harness comparing task success rate per-provider would catch
automatically instead of requiring a human to notice the wording felt off.

**A hang, not an error, defeating the whole multi-provider fallback (caught
the next day, when Gemini's API itself started hanging instead of
returning an error):** `_call_llm_with_fallback` only ever caught explicit
`ClientError`/`ServerError` exceptions from the Gemini SDK, on the
assumption that a broken Gemini call would always come back as one of
those. It doesn't: with no client-side timeout configured, a Gemini request
that simply never returns hangs the whole request indefinitely, since a
hang isn't an exception at all, there's nothing to catch. Confirmed by
calling the Gemini SDK directly outside the app entirely, in isolation, and
watching it hang past 30 seconds with no error. Worse, adding a timeout via
the client constructor's `http_options` turned out not to be enough either:
passing a `config=` object on the actual `generate_content` call (needed to
pass `tools`) silently ignores the client-level default, falls back to some
much longer internal default, and raises a raw `httpx.ReadTimeout` when it
finally does give up, an exception type the existing handler never expected.
Fixed by setting `http_options` explicitly on every `GenerateContentConfig`
passed to the SDK, not just on the client, and by also catching
`httpx.TimeoutException` alongside `ServerError` so a timeout falls back to
Groq exactly like an explicit 5xx does. The lesson: an SDK's per-call config
object can silently override a client-level default instead of falling back
to it, so "I set it once at the top" isn't something to trust without
checking each call site that takes its own config.

**"20 requests/day" turned out to be true, just not for the model name
implied (caught while trying to work around persistent quota exhaustion):**
the model string used everywhere was `"gemini-flash-latest"`, an alias
Google repoints to whatever's newest. Catching the full 429 response body
(not just the summary message) showed it currently resolves to
`gemini-3.7-flash`, a newly released model still on a much stricter
free-tier daily quota than an established model gets - the 20/day figure
documented above was accurate, just for a model that changes out from under
the code without a code change. The quota is also strictly per-model
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), so a different model
name gets its own completely separate, unused daily allowance - it doesn't
matter that "flash" already ran out. Pinned to `gemini-3.6-flash` explicitly
instead: an older, more established model with much more free-tier
headroom, at the cost of not automatically riding along to whatever's
newest. A `"latest"` alias is convenient until the day it silently moves
you onto a model with different rate limits, different pricing, or
different behavior entirely - worth knowing before depending on one.

**A hung deploy with zero diagnostic information (caught immediately after
the fix above, on a Render redeploy):** the very next deploy failed with
nothing but "Timed out" - no traceback, no log line, because
`web_server.py`'s `startup()` spawns the MCP server subprocess and awaits
its handshake with no timeout of its own. If that hangs, the app never
finishes starting, so it never responds to Render's health check, and
Render's own watchdog is the only thing that ever reports anything, with no
insight into what actually got stuck. The same class of hang as the Gemini
one above, just one layer lower in the stack. Fixed the same way: wrapped
the MCP connection sequence in `asyncio.wait_for(..., timeout=30)` and raise
a specific `RuntimeError` on timeout, so a stuck subprocess handshake fails
fast with a real message in the logs instead of silently stalling the
platform's own health check.

**The Gemini timeout was tuned for the wrong network (caught by reading
Render's own logs, since local testing kept succeeding while the live
deploy kept failing):** the 15s `GEMINI_TIMEOUT_MS` was set based on local
testing, where Gemini consistently answered in 2-3 seconds. On Render,
production logs showed `[Gemini unavailable after retries - falling back to
Groq]` on requests that should have succeeded - meaning Gemini was timing
out on every retry attempt specifically when called from Render's network,
not hanging forever, just answering slower than 15s allowed for. The
fallback logic itself was working exactly as designed; the timeout was just
cutting off real, would-have-succeeded responses before they could finish,
forcing an unnecessary fallback to the less reliable Groq path. Raised to
25s. The debugging lesson: a timeout value tuned against one network path
(a local machine) doesn't necessarily hold for another (a cloud host's
route to the same API) - "it responds in 2s for me" isn't the same claim as
"it responds in 2s from anywhere."

**Raising the backend's timeout quietly moved the same risk to the frontend
(caught via a user report of the chat input staying disabled for several
minutes):** `frontend/app.js` had no timeout of its own on the `/chat`
fetch, on the assumption the backend would always come back reasonably
quickly. Raising `GEMINI_TIMEOUT_MS` above also raised the backend's
worst case: three Gemini retries at the new, longer timeout plus backoff
before ever reaching Groq, well over a minute in the worst case. With no
client-side timeout, the input stayed disabled that entire time with only a
static "Thinking..." label, indistinguishable from actually being stuck.
Fixed by adding an `AbortController`-based timeout to every backend fetch
(bounded above the backend's own worst case, so it only fires on a genuine
network-level hang), a distinct "request timed out" message instead of a
generic error, and updating the typing indicator's text after 12 seconds so
a long-but-healthy wait doesn't read the same as a frozen page. The lesson
repeats from the note above: fixing a timeout on one side of a request can
just relocate the same unbounded-wait problem to the other side.

**Retrying a persistently unavailable provider just multiplies the wait,
it doesn't recover anything (caught via a user report and the backend's
own logs, after a 2-day gap ruled out quota as the cause):** even with the
client-side timeout above in place, a real request still timed out at 100
seconds. The logs showed why: `[Gemini unavailable - falling back to Groq]`
printed twice in a row for a single chat turn, each one representing a full
3-attempt retry loop at the (now 25s) timeout plus backoff - up to ~78s -
before ever reaching Groq. A single user turn makes at least two separate
LLM calls (decide which tool to call, then write the final answer from the
tool's result), and each one independently paid that same worst-case cost.
Retrying only helps against genuinely transient failures; here Gemini was
failing the same way on every attempt, so the retries were pure overhead,
not resilience. Reduced `max_retries` from 3 to 1: try Gemini once, and
fall back to Groq immediately on any failure. This does trade away
recovering from a real transient 503 within the same call - the original
motivation for retrying at all - but a persistent-unavailability scenario
that compounds across every LLM call in a turn is the more damaging failure
mode in practice, and Groq remains available as an immediate fallback
either way. The broader lesson: a fix that's correct for one call in
isolation (give Gemini a fair, longer timeout) can still be wrong in
aggregate once you account for how many times it runs per user-visible
action.

**The real root cause of the recurring Gemini failures: every currently
available free-tier model has the same tiny daily cap (caught with a
temporary diagnostic endpoint, since Render's free tier has no shell
access to test connectivity directly):** added a throwaway `/debug/gemini`
route that called Gemini directly from the deployed instance and returned
the raw result. It came back with `RESOURCE_EXHAUSTED`, `limit: 20`, for
`gemini-3.6-flash` - the exact same 20/day ceiling as `gemini-3.7-flash`,
the model pinning was meant to get away from. Google's generous 250
requests/day free tier belongs to `gemini-2.5-flash`, a model this project
can no longer even access (deprecated for new users, confirmed earlier).
Every currently-available Gemini flash model apparently launches on the
same restrictive 20/day introductory quota - there was no better model to
pin to. This means Gemini isn't the reliable provider here at all: it's the
scarce one, exhausted by any real usage in minutes, while Groq's fallback
has stayed available and, in controlled side-by-side testing (5/5 and 3/3
tool-calling trials), was performing more reliably than the intermittent
live failures suggested. The debug route was removed once this was
answered - it only existed to get visibility Render's free tier doesn't
otherwise provide.

**Swapped which provider is primary, given the finding above:** with every
Gemini model capped at 20 requests/day, Gemini was never going to be
"available most of the time" no matter which model it was pinned to -
that's a hard ceiling, not something a code fix works around. Groq has been
the consistently available provider all along; the architecture just never
reflected that. `_call_llm_with_fallback` now tries Groq first and only
calls Gemini if Groq itself raises - refactored into two small, symmetric
`_call_groq`/`_call_gemini` helpers so the fallback direction is a five-line
diff, not a rewrite. The exponential-backoff retry logic was removed
entirely in the process: retrying the *same* provider stopped making sense
once a second, independent provider is one function call away - if Groq
fails, trying Gemini immediately is strictly better than retrying Groq
first. Caught one side effect while re-running the eval harness against
this: `test_no_hallucinated_checkout` failed on a perfectly correct decline
("doesn't handle... checkout") because the check's regex didn't recognize
that phrasing - broadened the pattern rather than treating it as a real
regression, since the actual agent behavior was right and only the test's
pattern-matching was too narrow.

**Groq being primary fixed availability, not per-request accuracy - those
are different problems (caught immediately after the swap, on the very
first live re-test):** a live request still failed right after the swap,
with a plausible-sounding "wasn't able to retrieve any items" excuse. The
logs showed something notable by its absence: no `[Groq unavailable...]`
line at all - the API call itself succeeded, the model just chose not to
call `search_garments` this particular time and fabricated an excuse for it
instead of trying or admitting uncertainty. Not an infrastructure failure;
genuine model non-determinism, the same class of issue as the hallucinated
checkout flow. Since Groq isn't quota-scarce, this is cheap to mitigate: if
the first tool-decision call comes back with no tool call, take one more
independent, *unforced* sample at the same decision before falling through
to a text response. Deliberately not `tool_choice="required"` - forcing a
tool call would break the legitimate no-tool-needed cases (the checkout
decline chief among them), so this only gives the model a second try at the
same, unforced choice. Verified the checkout-decline test still passes
after adding this, and 3/3 manual retries of the flagship query succeeded
cleanly. This won't reach 100% - it's still a probabilistic model, just now
sampled twice instead of once - but it directly reduces the exact failure
mode observed, at effectively no cost.

**"Groq isn't quota-scarce" was wrong - it has a real, tighter constraint
than assumed: 8000 tokens per minute (caught by bumping retries to 3 and
immediately triggering the exact cascading failure being fixed):**
measuring a ~20-25% per-attempt miss rate on a bare "tshirts" query and
reasoning "retries are free since Groq has no daily cap" led to raising the
unforced-retry count from 1 to 3. Stress-testing that change surfaced a
real `RateLimitError`: `tokens per minute (TPM): Limit 8000` on this
account. Every retry re-sends the full conversation history and tool
schemas, so tripling the attempts roughly tripled the token cost of every
ambiguous turn - and the resulting rate-limit forced a fallback to Gemini,
which was *also* mid-timeout at that exact moment, cascading into the
"having trouble reaching my services" last-resort message. The system
degraded exactly as designed under a genuine dual-provider failure; the
bug was causing that failure in the first place by treating "not
quota-limited" and "not rate-limited" as the same claim. Reverted to a
single retry (2 attempts total). The lesson: "no daily request cap" is not
the same property as "safe to call in a tight loop" - a provider can be
scarce along an axis (tokens/minute) that a request-count framing misses
entirely, and the fix for one constraint (Gemini's daily quota) doesn't
transfer to a different constraint on a different provider without
checking it actually holds.

**The real root cause of most "technical issue" fabrications: a blank
string looks exactly like a system failure to the model (caught by
reproducing a user's real multi-turn conversation line by line until the
failure appeared):** `search_garments` returning zero matches is a
perfectly legitimate outcome - a gold maxi dress under $100 genuinely
doesn't exist in this catalog. But `tools_node` built the follow-up prompt
as `"Tool 'search_garments' result:\n{result_text}..."`, and for an empty
list, `result_text` was a bare empty string - confirmed directly by calling
the MCP tool and inspecting the raw result. The model was being handed a
prompt that read, verbatim, `"Tool 'search_garments' result:\n\n\nRespond
to the user naturally based on this."`, with nothing after "result:" at
all. Every "technical issue" and "unable to access the catalog" fabrication
chased throughout this session was plausibly this exact case, misdiagnosed
as provider flakiness for hours: the model wasn't malfunctioning, it was
being handed a genuinely ambiguous, content-free message and doing the
same thing it did with the hallucinated checkout flow - inventing a
plausible-sounding explanation instead of admitting uncertainty about
what a blank string meant. Fixed with one `if not result_text.strip()`
check that substitutes an explicit "empty result - zero items matched"
message. Retested the user's exact real conversation that had triggered
it, verbatim, and got an honest "I couldn't find any golden maxi dresses
under $100" instead. The broader lesson, sitting under nearly every finding
in this file: an LLM asked to interpret an ambiguous or missing input will
confidently fill the gap with something false-sounding rather than flag
the ambiguity, so the fix is almost never "make the model try harder" -
it's removing the ambiguity from what it's given in the first place.

**Free-tier reliability has a hard floor code can't fix - added a
client-side send cooldown as the honest response to that:** every
provider-side fix in this file (timeouts, retries, primary/fallback order)
improved *how gracefully* the app handles running out of headroom, but none
of them raise the headroom itself - Gemini's 20/day cap and Groq's 8000 TPM
budget are real ceilings, and a burst of messages sent in quick succession
(automated testing or an impatient user re-sending) is exactly what
exhausts them fast enough to cascade into both providers failing at once.
The actual fix for that is either paying for usage on one provider (removes
the ceiling entirely) or not hitting the ceiling in the first place. Added
a 3-second cooldown on the send button after every response, applied
uniformly whether the turn succeeded or errored, since either path may have
already spent tokens. It's a small, honest mitigation, not a cure: normal
conversational pacing was never the problem, rapid-fire bursts were, and
this removes the ability to accidentally trigger one from the UI itself.

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
all. Confirmed directly: a temporary `/debug/mcp` route that called
`search_garments` through the live subprocess, bypassing the LLM entirely,
returned `"Error executing tool search_garments: 'DATABASE_URL'"` -
a plain `KeyError`, not a provider issue. Fixed by passing
`env={"DATABASE_URL": os.environ["DATABASE_URL"]}` explicitly to
`StdioServerParameters`. This also reframes a chunk of this session's
provider-flakiness investigation: some of the "technical issue" replies
blamed on Groq/Gemini were likely the model *honestly* reporting a real,
underlying tool error it wasn't given clean language to explain, rather
than fabricating one - the empty-result fix earlier addressed the
ambiguous-input version of that same pattern; this was the same symptom
with an actual error behind it. Exactly why the failure wasn't 100%
consistent across the whole session remains unclear - worth treating as
resolved based on the direct confirmation and retest, not as fully
explained.

**Eval harness false failures (found while first building it):** the first
run showed 2 false failures, not real bugs - one check required a straight
apostrophe and missed the model's curly `'`, the other required an exact
phrase match where the model had (correctly) paraphrased the real care
instructions. Fixed by matching on apostrophe-agnostic patterns and keyword
overlap instead of exact substrings - a reminder that an eval harness needs
almost as much care against false failures as the product code needs
against real ones.
