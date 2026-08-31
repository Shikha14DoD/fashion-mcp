import asyncio
import time
import uuid
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google.genai import types
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from graph_state import AgentState
from graph_nodes import make_agent_node, make_tools_node, confirm_node, mcp_tools_to_groq_schema, strip_hidden_args
import graph_nodes

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for a demo; a real production app would restrict this
    allow_methods=["*"],
    allow_headers=["*"],
)

server_params = StdioServerParameters(command="python", args=["server.py"])

def mcp_tool_to_gemini_schema(tool):
    return types.Tool(function_declarations=[types.FunctionDeclaration(
        name=tool.name, description=tool.description, parameters=tool.input_schema,
    )])

_graph_app = None
_session_cm = None
_session = None
checkpointer = InMemorySaver()

MCP_STARTUP_TIMEOUT_S = 30  # a hung subprocess handshake should fail loudly, not stall the deploy's health check

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ConfirmRequest(BaseModel):
    session_id: str
    answer: str

async def _connect_mcp():
    global _session_cm, _session
    _session_cm = stdio_client(server_params)
    read, write = await _session_cm.__aenter__()
    _session = ClientSession(read, write)
    await _session.__aenter__()
    await _session.initialize()
    return await _session.list_tools()

@app.on_event("startup")
async def startup():
    global _graph_app
    try:
        mcp_tools = await asyncio.wait_for(_connect_mcp(), timeout=MCP_STARTUP_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            f"MCP server subprocess didn't respond within {MCP_STARTUP_TIMEOUT_S}s - "
            "failing startup instead of hanging the deploy's health check"
        ) from e

    gemini_tools = [types.Tool(function_declarations=[
        types.FunctionDeclaration(name=t.name, description=t.description, parameters=strip_hidden_args(t.input_schema))
        for t in mcp_tools.tools
    ])]
    groq_tools = mcp_tools_to_groq_schema(mcp_tools.tools)

    graph = StateGraph(AgentState)
    graph.add_node("agent", make_agent_node(gemini_tools, groq_tools))
    graph.add_node("tools", make_tools_node(_session))
    graph.add_node("confirm", confirm_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent",
        lambda s: END if s.get("pending_action") is None else ("tools" if s["pending_action"].get("auto") else "confirm"),
        {"tools": "tools", "confirm": "confirm", END: END})
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("confirm",
        lambda s: "tools" if (s.get("pending_action") or {}).get("approved") else "agent",
        {"tools": "tools", "agent": "agent"})

    _graph_app = graph.compile(checkpointer=checkpointer)

@app.post("/session")
def new_session():
    return {"session_id": str(uuid.uuid4())}

@app.get("/debug/gemini")
async def debug_gemini():
    """Temporary: calls Gemini directly from this exact runtime so we can see
    whether Render's network path to Gemini is the problem, independent of
    the full agent pipeline. Remove once the Render-vs-local mystery is solved."""
    def _call():
        t0 = time.time()
        try:
            resp = graph_nodes._client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[{"role": "user", "parts": [{"text": "say hi in 3 words"}]}],
            )
            return {"success": True, "seconds": round(time.time() - t0, 2), "text": resp.candidates[0].content.parts[0].text}
        except Exception as e:
            return {"success": False, "seconds": round(time.time() - t0, 2), "error_type": type(e).__name__, "error": str(e)[:500]}

    return await asyncio.to_thread(_call)

@app.post("/chat")
async def chat(req: ChatRequest):
    config = {"configurable": {"thread_id": req.session_id}}
    state = {"messages": [{"role": "user", "content": req.message}], "api_key": "demo_key_123", "pending_action": None}
    result = await _graph_app.ainvoke(state, config=config)

    if "__interrupt__" in result:
        data = result["__interrupt__"][0].value
        return {"type": "confirmation_required", "tool": data["tool"], "args": data["args"]}

    return {"type": "message", "text": result["messages"][-1].content}

@app.post("/confirm")
async def confirm(req: ConfirmRequest):
    config = {"configurable": {"thread_id": req.session_id}}
    result = await _graph_app.ainvoke(Command(resume=req.answer), config=config)
    return {"type": "message", "text": result["messages"][-1].content}