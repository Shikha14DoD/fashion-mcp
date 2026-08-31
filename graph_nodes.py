import os
import json
import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError
from groq import Groq

SYSTEM_PROMPT = """You are a styling assistant for a fashion catalog. You have
access to exactly four tools: search_garments, check_availability,
get_care_instructions, and save_to_wishlist. You have no other capabilities.

Critically:
- You cannot process payments, checkouts, or orders. No such tool exists.
- Never invent URLs, links, order confirmations, or tracking numbers.
- If asked to purchase, checkout, or pay, clearly state that this system only
  supports search, stock checks, and wishlists — it cannot process a purchase.
- Never fabricate information not returned by a tool call.
- Do not use em dashes (—) in your responses. Use short hyphens (-), commas,
  or separate sentences instead."""


load_dotenv()
_groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

GEMINI_TIMEOUT_MS = 25000  # a hang here would otherwise block the Gemini fallback path indefinitely
_client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"],
    http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
)

WRITE_TOOLS = {"save_to_wishlist"}  # tools that require human confirmation

# Args the client injects itself (e.g. the authenticated user's api_key in
# tools_node below) and that the LLM should never be asked to supply.
HIDDEN_ARGS = {"api_key"}

def strip_hidden_args(schema):
    """Remove server-injected args from a tool's schema before it's shown to the LLM."""
    schema = dict(schema)
    schema["properties"] = {
        k: v for k, v in schema.get("properties", {}).items() if k not in HIDDEN_ARGS
    }
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in HIDDEN_ARGS]
    return schema

class QuotaExhausted(Exception):
    """Raised when the Gemini free-tier daily quota is used up."""
    pass

def _groq_tool_decision(groq_messages, groq_tools):
    groq_response = _groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=groq_messages,
        tools=groq_tools,
    )
    return groq_response.choices[0].message

def _call_groq(groq_messages, groq_tools):
    msg = _groq_tool_decision(groq_messages, groq_tools)
    if not msg.tool_calls:
        # Unforced retry: not forcing tool_choice, since that would wrongly push a
        # tool call on turns that legitimately need none (e.g. the checkout decline).
        # Just a second, independent sample at the same decision - cheap since Groq
        # isn't quota-limited, and it recovers a real fraction of one-off misses.
        msg = _groq_tool_decision(groq_messages, groq_tools)
    if msg.tool_calls:
        call = msg.tool_calls[0]
        return {"provider": "groq", "tool_name": call.function.name,
                "tool_args": json.loads(call.function.arguments)}
    text = stream_groq_text(groq_messages, groq_tools)
    if not text.strip():
        text = "I'm not sure how to respond to that - could you rephrase?"
    return {"provider": "groq", "text": text}

def _call_gemini(gemini_messages, gemini_tools):
    response = _client.models.generate_content(
        model="gemini-3.6-flash",
        contents=gemini_messages,
        config=types.GenerateContentConfig(
            tools=gemini_tools,
            http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
        ),
    )
    part = response.candidates[0].content.parts[0]
    if part.function_call:
        return {"provider": "gemini", "tool_name": part.function_call.name,
                "tool_args": dict(part.function_call.args)}
    text = stream_gemini_text(gemini_messages)
    if not text.strip():
        text = "I'm not sure how to respond to that - could you rephrase?"
    return {"provider": "gemini", "text": text}

def _call_llm_with_fallback(gemini_messages, gemini_tools, groq_tools):
    """Groq is primary: it's the provider that's actually available at real usage
    volumes, since every current free-tier Gemini model caps out at 20 requests/day.
    Gemini is the fallback for the rare case Groq itself fails."""
    groq_messages = [
        {"role": "user" if m["role"] == "user" else "assistant", "content": m["parts"][0]["text"]}
        for m in gemini_messages
    ]
    try:
        return _call_groq(groq_messages, groq_tools)
    except Exception as e:
        print(f"[Groq unavailable ({type(e).__name__}: {e}) - falling back to Gemini]")

    try:
        return _call_gemini(gemini_messages, gemini_tools)
    except ClientError as e:
        if "RESOURCE_EXHAUSTED" in str(e):
            print("[Gemini also unavailable: quota exhausted]")
        else:
            print(f"[Gemini also unavailable ({type(e).__name__}: {e})]")
    except (ServerError, httpx.TimeoutException) as e:
        print(f"[Gemini also unavailable ({type(e).__name__}: {e})]")

    return {"provider": "none", "text": "I'm having trouble reaching my services right now - please try again in a moment."}

def stream_gemini_text(gemini_messages):
    """Stream a plain-text response from Gemini."""
    full_text = ""
    stream = _client.models.generate_content_stream(
        model="gemini-3.6-flash",
        contents=gemini_messages,
        config=types.GenerateContentConfig(
            http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
        ),
    )
    for chunk in stream:
        if chunk.text:
            full_text += chunk.text
    return full_text

def stream_groq_text(groq_messages, groq_tools):
    """Stream a plain-text response from Groq."""
    full_text = ""
    stream = _groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=groq_messages,
        tools=groq_tools,
        tool_choice="none",
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            full_text += delta
    return full_text

def make_agent_node(gemini_tools, groq_tools):
    """Returns a node function with both providers' tool schemas already baked in."""

    def agent_node(state):
        gemini_messages = [
            {"role": "user", "parts": [{"text": SYSTEM_PROMPT}]},
            {"role": "model", "parts": [{"text": "Understood."}]},
        ] + [
            {"role": "user" if m.type == "human" else "model", "parts": [{"text": m.content}]}
            for m in state["messages"]
        ]
        result = _call_llm_with_fallback(gemini_messages, gemini_tools, groq_tools)

        if "tool_name" in result:
            tool_name = result["tool_name"]
            tool_args = result["tool_args"]
            note = f"[{result['provider']} calling {tool_name}]"

            if tool_name in WRITE_TOOLS:
                return {
                    "messages": [{"role": "assistant", "content": note}],
                    "pending_action": {"tool": tool_name, "args": tool_args},
                }
            else:
                return {
                    "messages": [{"role": "assistant", "content": note}],
                    "pending_action": {"tool": tool_name, "args": tool_args, "auto": True},
                }
        else:
            return {
                "messages": [{"role": "assistant", "content": result["text"]}],
                "pending_action": None,
            }

    return agent_node

def make_tools_node(session):
    """Returns a node function with the MCP session already baked in."""

    async def tools_node(state):
        action = state["pending_action"]
        tool_name = action["tool"]
        tool_args = dict(action["args"])

        # inject the authenticated user's api_key for tools that need it
        if tool_name == "save_to_wishlist":
            tool_args["api_key"] = state["api_key"]

        result = await session.call_tool(tool_name, tool_args)
        result_text = "\n".join(c.text for c in result.content)
        if not result_text.strip():
            # An empty list is a legitimate "zero matches" result, not a failure -
            # but a blank string here reads exactly like one to the model, which
            # was fabricating a "technical issue" excuse instead of just saying so.
            result_text = "[] (empty result - the query ran successfully but matched zero items)"

        return {
            "messages": [{
                "role": "user",
                "content": f"Tool '{tool_name}' result:\n{result_text}\n\nRespond to the user naturally based on this."
            }],
            "pending_action": None,
        }

    return tools_node

from langgraph.types import interrupt

def confirm_node(state):
    action = state["pending_action"]
    answer = interrupt({
        "type": "confirmation_required",
        "tool": action["tool"],
        "args": action["args"],
    })

    if answer in ("yes", "y"):
        return {"pending_action": {**action, "approved": True}}
    else:
        return {
            "messages": [{
                "role": "user",
                "content": "The user declined this action. Do not proceed. Ask what they'd like instead."
            }],
            "pending_action": None,
        }

def mcp_tools_to_groq_schema(mcp_tools):
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": strip_hidden_args(t.input_schema),
        },
    } for t in mcp_tools]