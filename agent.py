import asyncio
import os
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google import genai
from google.genai import types
import time
from google.genai.errors import ServerError

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

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model="gemini-flash-latest",
                        contents=user_request,
                        config=types.GenerateContentConfig(tools=gemini_tools),
                    )
                    break
                except ServerError as e:
                    if attempt == max_retries - 1:
                        print(f"Gemini unavailable after {max_retries} attempts: {e}")
                        return
                    wait = 2 ** attempt
                    print(f"Gemini overloaded, retrying in {wait}s...")
                    time.sleep(wait)

            part = response.candidates[0].content.parts[0]
            if part.function_call:
                tool_name = part.function_call.name
                tool_args = dict(part.function_call.args)
                print(f"Gemini wants to call: {tool_name}")
                print(f"With arguments: {tool_args}")

                result = await session.call_tool(tool_name, tool_args)
                print("\nActual tool result:")
                for content in result.content:
                    print(content.text)
            else:
                print("Gemini responded with text instead:", part.text)

if __name__ == "__main__":
    asyncio.run(main())