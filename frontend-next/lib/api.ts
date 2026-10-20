// Base URL of the FastAPI backend (web_server.py). Local dev hits localhost;
// anywhere else it points at the deployed Render backend.
export function getApiBase(): string {
  const isLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  return isLocal ? "http://127.0.0.1:8000" : "https://fashion-mcp-backend.onrender.com";
}

export type ChatResponse =
  | { type: "message"; text: string }
  | { type: "confirmation_required"; tool: string; args: Record<string, unknown> };

export async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function createSession(apiBase: string): Promise<string> {
  const res = await fetch(`${apiBase}/session`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to create session (${res.status})`);
  const data = await res.json();
  return data.session_id as string;
}

export async function postChat(
  apiBase: string,
  sessionId: string,
  message: string,
  timeoutMs: number
): Promise<ChatResponse> {
  const res = await fetchWithTimeout(
    `${apiBase}/chat`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
    },
    timeoutMs
  );
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
  return (await res.json()) as ChatResponse;
}

export async function postConfirm(
  apiBase: string,
  sessionId: string,
  answer: "yes" | "no",
  timeoutMs: number
): Promise<ChatResponse> {
  const res = await fetchWithTimeout(
    `${apiBase}/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, answer }),
    },
    timeoutMs
  );
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
  return (await res.json()) as ChatResponse;
}
