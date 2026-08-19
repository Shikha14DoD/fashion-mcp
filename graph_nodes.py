import os
import time
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError
from groq import Groq
from groq import APIStatusError as GroqAPIError

SYSTEM_PROMPT = """You are a styling assistant for a fashion catalog. You have
access to exactly four tools: search_garments, check_availability,
get_care_instructions, and save_to_wishlist. You have no other capabilities.

Critically:
- You cannot process payments, checkouts, or orders. No such tool exists.
- Never invent URLs, links, order confirmations, or tracking numbers.
- If asked to purchase, checkout, or pay, clearly state that this system only
  supports search, stock checks, and wishlists — it cannot process a purchase.
- Never fabricate information not returned by a tool call."""


load_dotenv()
_groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

WRITE_TOOLS = {"save_to_wishlist"}  # tools that require human confirmation

class QuotaExhausted(Exception):
    """Raised when the Gemini free-tier daily quota is used up."""
    pass

def _call_groq_fallback(gemini_messages, groq_tools):
    """Convert Gemini-style message history into plain text and ask Groq instead."""
    groq_messages = []
    for m in gemini_messages:
        role = "user" if m["role"] == "user" else "assistant"
        groq_messages.append({"role": role, "content": m["parts"][0]["text"]})

    response = _groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=groq_messages,
        tools=groq_tools,
    )
    return response

def _call_llm_with_fallback(gemini_messages, gemini_tools, groq_tools, max_retries=3):
    """Try Gemini first (with retry on transient 503s). Fall back to Groq on
    quota exhaustion (429) or repeated Gemini failure."""
    for attempt in range(max_retries):
        try:
            response = _client.models.generate_content(
                model="gemini-flash-latest",
                contents=gemini_messages,
                config=types.GenerateContentConfig(tools=gemini_tools),
            )
            part = response.candidates[0].content.parts[0]
            if part.function_call:
                return {"provider": "gemini", "tool_name": part.function_call.name,
                        "tool_args": dict(part.function_call.args)}
            return {"provider": "gemini", "text": part.text}

        except ClientError as e:
            if "RESOURCE_EXHAUSTED" in str(e):
                print("[Gemini quota exhausted — falling back to Groq]")
                break
            raise
        except ServerError:
            if attempt == max_retries - 1:
                print("[Gemini unavailable after retries — falling back to Groq]")
                break
            time.sleep(2 ** attempt)

    groq_response = _call_groq_fallback(gemini_messages, groq_tools)
    msg = groq_response.choices[0].message
    if msg.tool_calls:
        call = msg.tool_calls[0]
        return {"provider": "groq", "tool_name": call.function.name,
                "tool_args": json.loads(call.function.arguments)}
    return {"provider": "groq", "text": msg.content}

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

        return {
            "messages": [{
                "role": "user",
                "content": f"Tool '{tool_name}' result:\n{result_text}\n\nRespond to the user naturally based on this."
            }],
            "pending_action": None,
        }

    return tools_node

def confirm_node(state):
    action = state["pending_action"]
    print(f"\n⚠️  The assistant wants to: {action['tool']} with {action['args']}")
    answer = input("Approve this action? (yes/no): ").strip().lower()

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
            "parameters": t.input_schema,
        },
    } for t in mcp_tools]