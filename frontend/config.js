// Base URL of the FastAPI backend (web_server.py).
// Local dev hits localhost; anywhere else it points at the deployed
// Render backend. If you rename the Render service, update the URL below.
const isLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname);
window.FASHION_MCP_API_BASE = isLocal
  ? "http://127.0.0.1:8000"
  : "https://fashion-mcp-backend.onrender.com";
