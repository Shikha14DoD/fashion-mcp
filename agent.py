import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google.genai import types
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from graph_state import AgentState
from graph_nodes import make_agent_node, make_tools_node, confirm_node, mcp_tools_to_groq_schema

server_params = StdioServerParameters(command="python", args=["server.py"])

def mcp_tool_to_gemini_schema(tool):
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters=tool.input_schema,
    )

def route_after_agent(state):
    action = state.get("pending_action")
    if action is None:
        return END
    if action.get("auto"):
        return "tools"
    return "confirm"

def route_after_confirm(state):
    action = state.get("pending_action")
    if action and action.get("approved"):
        return "tools"
    return "agent"

async def main():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()
            gemini_tools = [types.Tool(function_declarations=[
                mcp_tool_to_gemini_schema(t) for t in mcp_tools.tools
            ])]
            groq_tools = mcp_tools_to_groq_schema(mcp_tools.tools)

            graph = StateGraph(AgentState)
            graph.add_node("agent", make_agent_node(gemini_tools, groq_tools))
            graph.add_node("tools", make_tools_node(session))
            graph.add_node("confirm", confirm_node)

            graph.set_entry_point("agent")
            graph.add_conditional_edges("agent", route_after_agent,
                                         {"tools": "tools", "confirm": "confirm", END: END})
            graph.add_edge("tools", "agent")
            graph.add_conditional_edges("confirm", route_after_confirm,
                                         {"tools": "tools", "agent": "agent"})

            checkpointer = InMemorySaver()
            app = graph.compile(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "terminal-session"}}

            print("Fashion styling assistant (LangGraph). Type 'quit' to exit.\n")
            api_key = "demo_key_123"
            state = {"messages": [], "api_key": api_key, "pending_action": None}

            while True:
                user_input = input("You: ").strip()
                if user_input.lower() in ("quit", "exit"):
                    break
                if not user_input:
                    continue

                state["messages"].append({"role": "user", "content": user_input})
                result = await app.ainvoke(state, config=config)


                if "__interrupt__" in result:
                    interrupt_data = result["__interrupt__"][0].value
                    print(f"\n⚠️  The assistant wants to: {interrupt_data['tool']} with {interrupt_data['args']}")
                    answer = input("Approve this action? (yes/no): ").strip().lower()
                    result = await app.ainvoke(Command(resume=answer), config=config)

                state = result
                last = state["messages"][-1]
                if last.content.strip():
                    print()  # spacing after the streamed text, which has no trailing newline gap

if __name__ == "__main__":
    asyncio.run(main())