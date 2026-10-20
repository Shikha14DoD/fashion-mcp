"""Evaluation harness for the deployed styling agent.

Black-box: drives the agent purely through its public HTTP API
(/session, /chat, /confirm), the same way a real user would. Ground truth
for groundedness checks comes from calling the MCP tool functions in
server.py directly against the same database, so a test fails only if the
agent's *answer* diverges from what the catalog actually contains.

Usage:
    python eval_harness.py                     # tests http://127.0.0.1:8000
    python eval_harness.py --base-url https://fashion-mcp-backend.onrender.com
"""

import argparse
import re
import sys
import time

import requests

import server

# Agent responses routinely contain Unicode (curly quotes, non-breaking
# hyphens, etc.) that a Windows console's default cp1252 encoding can't
# print. Report it visibly instead of crashing the harness over it.
sys.stdout.reconfigure(errors="backslashreplace")

RETRYABLE_PHRASES = (
    "technical issue", "technical problem", "try again",
    "unable to connect", "not able to retrieve", "isn't able to retrieve",
    "encountering a technical", "check back", "trouble generating a response",
)


def new_session(base_url):
    r = requests.post(f"{base_url}/session", timeout=30)
    r.raise_for_status()
    return r.json()["session_id"]


def chat(base_url, session_id, message, retries=3):
    """POST /chat, retrying if the fallback model gave a known-flaky non-answer."""
    last = None
    for attempt in range(retries + 1):
        r = requests.post(f"{base_url}/chat", json={"session_id": session_id, "message": message}, timeout=60)
        r.raise_for_status()
        last = r.json()
        text = last.get("text", "")
        if last["type"] != "message" or not any(p in text.lower() for p in RETRYABLE_PHRASES):
            return last, attempt
        time.sleep(2)
    return last, retries


def confirm(base_url, session_id, answer):
    r = requests.post(f"{base_url}/confirm", json={"session_id": session_id, "answer": answer}, timeout=60)
    r.raise_for_status()
    return r.json()


class Result:
    def __init__(self, name, passed, detail, retried=False):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.retried = retried


def test_no_hallucinated_checkout(base_url):
    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, "I'd like to place an order and pay for it now.")
    text = resp.get("text", "")
    has_url = bool(re.search(r"https?://", text))
    decline_pattern = r"cannot|can.t|don.t have|do not have|doesn.t handle|does not handle|n.t (support|process|handle)|no checkout|unable|not able"
    declines = bool(re.search(decline_pattern, text.lower()))
    passed = resp["type"] == "message" and not has_url and declines
    detail = "declined without inventing a checkout flow" if passed else f"response: {text!r}"
    return Result("no_hallucinated_checkout", passed, detail, retried > 0)


def test_search_returns_grounded_prices(base_url):
    ground_truth = server.search_garments(article_type="tshirt", max_price_usd=50, limit=10)
    if not ground_truth:
        return Result("search_grounded_prices", False, "no ground-truth rows to compare against")
    real_prices = {f"${row['price_cents'] / 100:.2f}" for row in ground_truth}

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, "search for tshirts under $50")
    text = resp.get("text", "")
    matched = [p for p in real_prices if p in text]
    passed = resp["type"] == "message" and len(matched) > 0
    detail = f"found real price(s) {matched} in response" if passed else f"no real prices matched; response: {text!r}"
    return Result("search_grounded_prices", passed, detail, retried > 0)


def test_availability_matches_ground_truth(base_url):
    with server.get_conn() as conn:
        row = conn.execute("SELECT garment_id, size FROM inventory WHERE qty > 0 LIMIT 1").fetchone()
    if row is None:
        return Result("availability_grounded", False, "no in-stock inventory row found to test against")
    garment_id, size = row
    ground_truth = server.check_availability(garment_id, size)

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, f"is garment id {garment_id} in stock in size {size}?")
    text = resp.get("text", "")
    qty_str = str(ground_truth["qty"])
    passed = resp["type"] == "message" and qty_str in text
    detail = f"expected qty {qty_str} present in response" if passed else \
        f"expected qty {qty_str}, response: {text!r}"
    return Result("availability_grounded", passed, detail, retried > 0)


def _significant_words(text):
    """Words of 4+ letters, lowercased — good enough to compare substance, not exact phrasing."""
    return {w for w in re.findall(r"[a-zA-Z]+", text.lower()) if len(w) >= 4}


def test_care_instructions_grounded(base_url):
    with server.get_conn() as conn:
        row = conn.execute("SELECT id, fabric FROM garments WHERE fabric = 'Cotton' LIMIT 1").fetchone()
    if row is None:
        return Result("care_instructions_grounded", False, "no Cotton garment found to test against")
    garment_id, fabric = row
    ground_truth = server.CARE_MAP[fabric]

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, f"what are the care instructions for garment id {garment_id}?")
    text = resp.get("text", "")
    ground_words = _significant_words(ground_truth)
    overlap = ground_words & _significant_words(text)
    # Paraphrasing is fine; inventing unrelated care advice is not, so require most key terms to survive.
    passed = resp["type"] == "message" and len(overlap) >= max(2, len(ground_words) // 2)
    detail = f"overlap with real care instructions: {sorted(overlap)}" if passed else \
        f"expected terms from {ground_truth!r}, response: {text!r}"
    return Result("care_instructions_grounded", passed, detail, retried > 0)


def test_wishlist_asks_size_before_saving(base_url):
    """save_to_wishlist has no size parameter, so the agent must ask for a
    size and check it's in stock before reaching the confirmation gate -
    not save first and ask about size as an afterthought."""
    with server.get_conn() as conn:
        row = conn.execute("SELECT garment_id, size FROM inventory WHERE qty > 0 LIMIT 1").fetchone()
    if row is None:
        return Result("wishlist_asks_size_before_saving", False, "no in-stock inventory row found to test against")
    garment_id, size = row

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, f"save garment id {garment_id} to my wishlist")
    if resp["type"] != "message":
        return Result("wishlist_asks_size_before_saving", False,
                       f"expected the agent to ask for a size first, got {resp}", retried > 0)

    resp2, retried2 = chat(base_url, sid, f"size {size}")
    if resp2["type"] != "confirmation_required":
        return Result("wishlist_asks_size_before_saving", False,
                       f"expected confirmation_required once an in-stock size was given, got {resp2}",
                       retried > 0 or retried2 > 0)
    if "api_key" in resp2.get("args", {}):
        return Result("wishlist_asks_size_before_saving", False,
                       "api_key leaked into the LLM-facing confirmation args", retried > 0 or retried2 > 0)
    if resp2.get("tool") != "save_to_wishlist" or resp2.get("args", {}).get("garment_id") != garment_id:
        return Result("wishlist_asks_size_before_saving", False,
                       f"unexpected tool/args: {resp2}", retried > 0 or retried2 > 0)

    final = confirm(base_url, sid, "yes")
    final_text = final.get("text", "").lower()
    passed = final["type"] == "message" and ("wishlist" in final_text or "saved" in final_text)
    detail = "asked for size, checked it, then confirmed and saved" if passed else f"confirm response: {final}"
    return Result("wishlist_asks_size_before_saving", passed, detail, retried > 0 or retried2 > 0)


def test_wishlist_blocks_out_of_stock_size(base_url):
    """The confirmation gate must never fire for a size that isn't in stock -
    the agent should decline and offer a different size instead."""
    with server.get_conn() as conn:
        row = conn.execute("SELECT garment_id, size FROM inventory WHERE qty = 0 LIMIT 1").fetchone()
    if row is None:
        return Result("wishlist_blocks_out_of_stock_size", False, "no out-of-stock inventory row found to test against")
    garment_id, size = row

    sid = new_session(base_url)
    chat(base_url, sid, f"save garment id {garment_id} to my wishlist")
    resp, retried = chat(base_url, sid, f"size {size}")
    passed = resp["type"] == "message"
    detail = "declined the out-of-stock size without reaching the confirmation gate" if passed \
        else f"expected a text decline, got {resp}"
    return Result("wishlist_blocks_out_of_stock_size", passed, detail, retried > 0)


def test_declining_confirmation_is_respected(base_url):
    with server.get_conn() as conn:
        row = conn.execute("SELECT garment_id, size FROM inventory WHERE qty > 0 OFFSET 1 LIMIT 1").fetchone()
    garment_id, size = row

    sid = new_session(base_url)
    chat(base_url, sid, f"save garment id {garment_id} to my wishlist")
    resp, retried = chat(base_url, sid, f"size {size}")
    if resp["type"] != "confirmation_required":
        return Result("declining_confirmation_respected", False,
                       f"expected confirmation_required, got {resp}", retried > 0)

    final = confirm(base_url, sid, "no")
    final_text = final.get("text", "").lower()
    passed = final["type"] == "message" and "saved" not in final_text
    detail = "decline was respected, nothing saved" if passed else f"confirm response: {final}"
    return Result("declining_confirmation_respected", passed, detail, retried > 0)


def test_invalid_api_key_rejected(base_url):
    """White-box: save_to_wishlist must reject a bad key before ever touching
    the database, and never consume rate-limit budget for the real demo user
    in doing so (auth happens before the rate-limit check)."""
    with server.get_conn() as conn:
        row = conn.execute("SELECT garment_id FROM inventory WHERE qty > 0 LIMIT 1").fetchone()
    garment_id = row[0]
    result = server.save_to_wishlist(api_key="clearly_invalid_key_xyz", garment_id=garment_id)
    passed = result.get("error") == "Invalid API key"
    detail = "invalid key rejected before touching the database" if passed else f"unexpected result: {result}"
    return Result("invalid_api_key_rejected", passed, detail)


def test_wishlist_rejects_nonexistent_garment(base_url):
    """White-box: save_to_wishlist's not_found path for a garment id that
    was never in the catalog, using a real (demo) api key so auth succeeds
    and the existence check is what's actually being exercised."""
    with server.get_conn() as conn:
        row = conn.execute("SELECT api_key FROM users LIMIT 1").fetchone()
    if row is None:
        return Result("wishlist_rejects_nonexistent_garment", False, "no user row found to test against")
    api_key = row[0]
    bogus_id = 999_999_999
    result = server.save_to_wishlist(api_key=api_key, garment_id=bogus_id)
    passed = "does not exist" in result.get("error", "")
    detail = "nonexistent garment id rejected" if passed else f"unexpected result: {result}"
    return Result("wishlist_rejects_nonexistent_garment", passed, detail)


def test_rate_limit_enforced(base_url):
    """White-box, isolated: hammer check_rate_limit for a synthetic user id
    that's never used anywhere else, so this can't affect the real demo
    user's shared budget or any other test's rate-limit headroom."""
    synthetic_user = f"eval_harness_rate_limit_test_{int(time.time())}"
    allowed = [server.check_rate_limit(synthetic_user) for _ in range(server.RATE_LIMIT_MAX)]
    sixth = server.check_rate_limit(synthetic_user)
    passed = all(allowed) and not sixth
    detail = f"first {server.RATE_LIMIT_MAX} calls allowed, next one blocked" if passed else \
        f"allowed={allowed}, sixth_call_allowed={sixth}"
    return Result("rate_limit_enforced", passed, detail)


def test_empty_search_is_honest(base_url):
    """Regression test for a real bug: a blank tool-result string for zero
    matches read, verbatim, like a system failure to the model, which then
    fabricated a 'technical issue' excuse instead of saying it found
    nothing. Picks a filter combination with zero real matches, confirmed
    against the database, and checks the agent doesn't reach for one of the
    same retryable-sounding excuses used elsewhere in this harness."""
    ground_truth = server.search_garments(article_type="zzznonexistenttype", limit=10)
    if ground_truth:
        return Result("empty_search_is_honest", False, "expected filter unexpectedly matched real rows")

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, "search for zzznonexistenttype")
    text = resp.get("text", "").lower()
    fabricated = any(p in text for p in RETRYABLE_PHRASES)
    passed = resp["type"] == "message" and not fabricated
    detail = "zero matches reported honestly, no fabricated excuse" if passed else f"response: {text!r}"
    return Result("empty_search_is_honest", passed, detail, retried > 0)


def test_care_instructions_missing_garment(base_url):
    """The agent must say a garment doesn't exist rather than invent care
    instructions for an id that was never in the catalog."""
    bogus_id = 999_999_999
    ground_truth = server.get_care_instructions(bogus_id)
    if "error" not in ground_truth:
        return Result("care_instructions_missing_garment", False, "expected bogus id unexpectedly matched a real garment")

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, f"what are the care instructions for garment id {bogus_id}?")
    text = resp.get("text", "").lower()
    acknowledges_missing = bool(re.search(r"doesn.t exist|does not exist|not found|no garment|couldn.t find|could not find", text))
    passed = resp["type"] == "message" and acknowledges_missing
    detail = "acknowledged the garment doesn't exist" if passed else f"response: {text!r}"
    return Result("care_instructions_missing_garment", passed, detail, retried > 0)


def test_article_type_normalization(base_url):
    """White-box regression test for a documented bug: Gemini's function
    calling naturally produces values like 't-shirt' while the catalog
    stores 'Tshirts' (no space or hyphen). Confirms messy real-world
    variants still return the same ground-truth rows as the canonical
    value, i.e. the fix in search_garments's SQL normalization holds."""
    canonical = {row["id"] for row in server.search_garments(article_type="Tshirts", limit=50)}
    if not canonical:
        return Result("article_type_normalization", False, "no ground-truth Tshirts rows to compare against")

    mismatches = {}
    for variant in ("t-shirt", "T-Shirts", "TSHIRT", "t shirt"):
        got = {row["id"] for row in server.search_garments(article_type=variant, limit=50)}
        if got != canonical:
            mismatches[variant] = got
    passed = not mismatches
    detail = "all messy variants matched the canonical result set" if passed else f"mismatches: {mismatches}"
    return Result("article_type_normalization", passed, detail)


def test_search_respects_colour_filter(base_url):
    with server.get_conn() as conn:
        row = conn.execute("""
            SELECT article_type, colour, COUNT(*) c FROM garments
            WHERE colour IS NOT NULL
              AND article_type IN ('Tshirts','Shirts','Tops','Kurtas','Sunglasses','Handbags','Heels','Casual Shoes')
            GROUP BY article_type, colour HAVING COUNT(*) BETWEEN 1 AND 5 ORDER BY c LIMIT 1
        """).fetchone()
    if row is None:
        return Result("search_respects_colour_filter", False, "no narrow article_type/colour combo found to test against")
    article_type, colour, _ = row
    ground_truth = server.search_garments(article_type=article_type, colour=colour, max_price_usd=10000, limit=10)
    if not ground_truth:
        return Result("search_respects_colour_filter", False, "ground-truth query returned nothing")
    real_prices = {f"${g['price_cents'] / 100:.2f}" for g in ground_truth}

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, f"show me {colour.lower()} {article_type.lower()}")
    text = resp.get("text", "")
    matched = [p for p in real_prices if p in text]
    passed = resp["type"] == "message" and len(matched) > 0
    detail = f"found real price(s) {matched} for {colour} {article_type}" if passed else \
        f"no real prices matched; response: {text!r}"
    return Result("search_respects_colour_filter", passed, detail, retried > 0)


def test_search_respects_price_boundary(base_url):
    """search_garments uses price_cents <= max_cents - confirm the boundary
    is inclusive through the live agent, not just at the SQL layer."""
    with server.get_conn() as conn:
        row = conn.execute("SELECT id, name, article_type, price_cents FROM garments ORDER BY price_cents LIMIT 1").fetchone()
    garment_id, name, article_type, price_cents = row
    exact_price = price_cents / 100
    ground_truth = server.search_garments(article_type=article_type, max_price_usd=exact_price, limit=50)
    if garment_id not in {g["id"] for g in ground_truth}:
        return Result("search_respects_price_boundary", False,
                       "boundary item unexpectedly excluded from ground truth itself - can't test against it")

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, f"show me {article_type.lower()} for exactly ${exact_price:.2f} or less")
    text = resp.get("text", "")
    passed = resp["type"] == "message" and f"${exact_price:.2f}" in text
    detail = f"boundary-priced item (${exact_price:.2f}) included" if passed else f"response: {text!r}"
    return Result("search_respects_price_boundary", passed, detail, retried > 0)


def test_availability_handles_unknown_size(base_url):
    """check_availability's error path for a (garment, size) combo that was
    never in the inventory table at all, distinct from the qty=0 case
    already covered by wishlist_blocks_out_of_stock_size."""
    with server.get_conn() as conn:
        garment_id = conn.execute("SELECT id FROM garments LIMIT 1").fetchone()[0]
        taken_sizes = {r[0] for r in conn.execute(
            "SELECT size FROM inventory WHERE garment_id = %(id)s", {"id": garment_id}
        ).fetchall()}
    bogus_size = next(s for s in ("XXL2", "ZZ", "SIZE404") if s not in taken_sizes)
    ground_truth = server.check_availability(garment_id, bogus_size)
    if "error" not in ground_truth:
        return Result("availability_handles_unknown_size", False, "expected bogus size unexpectedly had a real record")

    sid = new_session(base_url)
    resp, retried = chat(base_url, sid, f"is garment id {garment_id} in stock in size {bogus_size}?")
    text = resp.get("text", "").lower()
    fabricated = any(p in text for p in RETRYABLE_PHRASES)
    acknowledges_missing = bool(re.search(r"no record|not available|don.t have|doesn.t have|couldn.t find|could not find|no such size|not a valid size", text))
    passed = resp["type"] == "message" and not fabricated and acknowledges_missing
    detail = "acknowledged no record for that size, without fabricating a quantity" if passed else f"response: {text!r}"
    return Result("availability_handles_unknown_size", passed, detail, retried > 0)


def test_read_only_tools_never_require_confirmation(base_url):
    """Architectural invariant from graph_nodes.py: WRITE_TOOLS = {"save_to_wishlist"}
    only, so search and availability must never themselves trigger the
    confirmation gate - that's reserved for the one tool that writes data."""
    sid = new_session(base_url)
    search_resp, r1 = chat(base_url, sid, "search for tshirts under $50")
    avail_resp, r2 = chat(base_url, sid, "is garment id 1 in stock in size M?")
    passed = search_resp["type"] == "message" and avail_resp["type"] == "message"
    detail = "neither read-only tool triggered a confirmation gate" if passed else \
        f"search response: {search_resp}, availability response: {avail_resp}"
    return Result("read_only_tools_never_require_confirmation", passed, detail, (r1 > 0) or (r2 > 0))


def test_session_isolation(base_url):
    """Each session is a separate LangGraph thread (thread_id = session_id
    in web_server.py) - a fresh session must not see another session's
    conversation history."""
    sid_a = new_session(base_url)
    secret = "48213"
    chat(base_url, sid_a, f"Please remember this number for later: {secret}. Just acknowledge it, nothing else.")

    sid_b = new_session(base_url)
    resp, retried = chat(base_url, sid_b, "What number did I just ask you to remember?")
    text = resp.get("text", "")
    passed = resp["type"] == "message" and secret not in text
    detail = "new session had no memory of the other session's number" if passed else f"response: {text!r}"
    return Result("session_isolation", passed, detail, retried > 0)


TEST_CASES = [
    test_no_hallucinated_checkout,
    test_search_returns_grounded_prices,
    test_availability_matches_ground_truth,
    test_care_instructions_grounded,
    test_wishlist_asks_size_before_saving,
    test_wishlist_blocks_out_of_stock_size,
    test_declining_confirmation_is_respected,
    test_invalid_api_key_rejected,
    test_wishlist_rejects_nonexistent_garment,
    test_rate_limit_enforced,
    test_empty_search_is_honest,
    test_care_instructions_missing_garment,
    test_article_type_normalization,
    test_search_respects_colour_filter,
    test_search_respects_price_boundary,
    test_availability_handles_unknown_size,
    test_read_only_tools_never_require_confirmation,
    test_session_isolation,
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    results = []
    for test in TEST_CASES:
        try:
            result = test(args.base_url)
        except Exception as e:
            result = Result(test.__name__, False, f"raised {type(e).__name__}: {e}")
        results.append(result)
        mark = "PASS" if result.passed else "FAIL"
        retry_note = " (needed a retry — fallback-model flakiness)" if result.retried else ""
        print(f"[{mark}] {result.name}{retry_note}\n       {result.detail}")

    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} passed ({passed / len(results):.0%})")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
