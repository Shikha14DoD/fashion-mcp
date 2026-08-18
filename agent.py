import asyncio
import os
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google import genai
from google.genai import types

load_dotenv()

server_params = StdioServerParameters(
    command="python",
    args=["server.py"],
)

def mcp_tool_to_gemini_schema(tool):
    """Convert an MCP tool definition into the format Gemini's function-calling expects."""
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters=tool.input_schema,
    )

async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()

            gemini_tools = [
                types.Tool(function_declarations=[
                    mcp_tool_to_gemini_schema(t) for t in mcp_tools.tools
                ])
            ]

            user_request = "Find me blue t-shirts under $100"

            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=user_request,
                config=types.GenerateContentConfig(tools=gemini_tools),
            )

            part = response.candidates[0].content.parts[0]
            if part.function_call:
                print(f"Gemini wants to call: {part.function_call.name}")
                print(f"With arguments: {dict(part.function_call.args)}")
            else:
                print("Gemini responded with text instead:", part.text)

if __name__ == "__main__":
    asyncio.run(main())