const API_BASE = window.FASHION_MCP_API_BASE;

const chatWindow = document.getElementById("chat-window");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const newSessionBtn = document.getElementById("new-session-btn");

let sessionId = null;
let awaitingConfirmation = false;

function scrollToBottom() {
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function appendMessage(role, text) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  chatWindow.appendChild(row);
  scrollToBottom();
  return row;
}

function appendTyping() {
  const row = appendMessage("assistant typing", "Thinking…");
  return row;
}

function appendConfirmCard(tool, args) {
  const row = document.createElement("div");
  row.className = "message assistant";

  const card = document.createElement("div");
  card.className = "confirm-card";

  const title = document.createElement("div");
  title.className = "confirm-title";
  title.textContent = `Confirm action: ${tool}`;
  card.appendChild(title);

  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(args, null, 2);
  card.appendChild(pre);

  const actions = document.createElement("div");
  actions.className = "confirm-actions";

  const yesBtn = document.createElement("button");
  yesBtn.className = "confirm-yes";
  yesBtn.textContent = "Yes, do it";

  const noBtn = document.createElement("button");
  noBtn.className = "confirm-no";
  noBtn.textContent = "No, cancel";

  actions.appendChild(yesBtn);
  actions.appendChild(noBtn);
  card.appendChild(actions);
  row.appendChild(card);
  chatWindow.appendChild(row);
  scrollToBottom();

  const disableButtons = () => {
    yesBtn.disabled = true;
    noBtn.disabled = true;
  };

  yesBtn.addEventListener("click", () => {
    disableButtons();
    resolveConfirmation("yes");
  });
  noBtn.addEventListener("click", () => {
    disableButtons();
    resolveConfirmation("no");
  });
}

function setInputEnabled(enabled) {
  chatInput.disabled = !enabled;
  sendBtn.disabled = !enabled;
}

async function ensureSession() {
  const stored = sessionStorage.getItem("fashion_mcp_session_id");
  if (stored) {
    sessionId = stored;
    return;
  }
  const res = await fetch(`${API_BASE}/session`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to create session (${res.status})`);
  const data = await res.json();
  sessionId = data.session_id;
  sessionStorage.setItem("fashion_mcp_session_id", sessionId);
}

async function startNewSession() {
  sessionStorage.removeItem("fashion_mcp_session_id");
  chatWindow.innerHTML = "";
  awaitingConfirmation = false;
  setInputEnabled(true);
  appendMessage(
    "assistant",
    "Hi! Tell me what you're shopping for — an occasion, a budget, colours to avoid — and I'll search the catalog for you."
  );
  try {
    await ensureSession();
  } catch (err) {
    appendMessage("error", `Couldn't reach the backend: ${err.message}`);
  }
}

function handleResponse(data) {
  if (data.type === "confirmation_required") {
    awaitingConfirmation = true;
    appendConfirmCard(data.tool, data.args);
    setInputEnabled(false);
  } else {
    appendMessage("assistant", data.text);
  }
}

async function resolveConfirmation(answer) {
  const typingRow = appendTyping();
  try {
    const res = await fetch(`${API_BASE}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, answer }),
    });
    typingRow.remove();
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();
    awaitingConfirmation = false;
    setInputEnabled(true);
    handleResponse(data);
  } catch (err) {
    typingRow.remove();
    appendMessage("error", `Something went wrong: ${err.message}`);
    awaitingConfirmation = false;
    setInputEnabled(true);
  }
  chatInput.focus();
}

async function sendMessage(text) {
  appendMessage("user", text);
  const typingRow = appendTyping();
  setInputEnabled(false);
  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    typingRow.remove();
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();
    handleResponse(data);
  } catch (err) {
    typingRow.remove();
    appendMessage("error", `Something went wrong: ${err.message}`);
  } finally {
    if (!awaitingConfirmation) setInputEnabled(true);
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || awaitingConfirmation) return;
  chatInput.value = "";
  sendMessage(text);
});

newSessionBtn.addEventListener("click", () => {
  startNewSession();
});

(async function init() {
  setInputEnabled(false);
  try {
    await ensureSession();
    setInputEnabled(true);
    chatInput.focus();
  } catch (err) {
    appendMessage("error", `Couldn't reach the backend at ${API_BASE}: ${err.message}`);
  }
})();
