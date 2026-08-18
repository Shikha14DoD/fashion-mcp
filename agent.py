import asyncio
import os
import time
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google import genai
from google.genai import types
from google.genai.errors import ServerError

load_dotenv()

server_params = StdioServerParameters(
    command="python",
    args=["server.py"],
)

def mcp_tool_to_gemini_schema(tool):
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters=tool.input_schema,
    )

def call_gemini_with_retry(chat, message, max_retries=3):
    for attempt in range(max_retries):
        try:
            return chat.send_message(message)
        except ServerError as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"Gemini overloaded, retrying in {wait}s...")
            time.sleep(wait)

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

            chat = client.chats.create(
                model="gemini-flash-latest",
                config=types.GenerateContentConfig(tools=gemini_tools),
            )

            print("Fashion styling assistant. Type 'quit' to exit.\n")

            while True:
                user_input = input("You: ").strip()
                if user_input.lower() in ("quit", "exit"):
                    break
                if not user_input:
                    continue

                response = call_gemini_with_retry(chat, user_input)
                part = response.candidates[0].content.parts[0]

                if part.function_call:
                    tool_name = part.function_call.name
                    tool_args = dict(part.function_call.args)
                    print(f"[calling {tool_name} with {tool_args}]")

                    result = await session.call_tool(tool_name, tool_args)
                    result_text = "\n".join(c.text for c in result.content)

                    # send the tool result back so Gemini can respond in natural language
                    follow_up = call_gemini_with_retry(
                        chat,
                        f"Tool result:\n{result_text}\n\nSummarize this for the user in a friendly way."
                    )
                    print(f"Assistant: {follow_up.text}\n")
                else:
                    print(f"Assistant: {part.text}\n")

if __name__ == "__main__":
    asyncio.run(main())