const API_BASE = window.FASHION_MCP_API_BASE;
const REQUEST_TIMEOUT_MS = 100000; // longer than the backend's own worst-case retry/fallback time

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
  const bubble = row.querySelector(".bubble");
  const timer = setTimeout(() => {
    bubble.textContent = "Still working - this can take up to a minute under heavy load...";
  }, 12000);
  row.dataset.timerId = timer;
  return row;
}

function removeTyping(row) {
  clearTimeout(Number(row.dataset.timerId));
  row.remove();
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
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

async function ensureSessionWithRetry(attempts = 4, delayMs = 5000) {
  for (let i = 0; i < attempts; i++) {
    try {
      await ensureSession();
      return;
    } catch (err) {
      if (i === attempts - 1) throw err;
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
}

async function startNewSession() {
  sessionStorage.removeItem("fashion_mcp_session_id");
  chatWindow.innerHTML = "";
  awaitingConfirmation = false;
  setInputEnabled(true);
  appendMessage(
    "assistant",
    "Hi! Tell me what you're shopping for - an occasion, a budget, colours to avoid - and I'll search the catalog for you."
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
    const res = await fetchWithTimeout(
      `${API_BASE}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, answer }),
      },
      REQUEST_TIMEOUT_MS
    );
    removeTyping(typingRow);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();
    awaitingConfirmation = false;
    setInputEnabled(true);
    handleResponse(data);
  } catch (err) {
    removeTyping(typingRow);
    const message = err.name === "AbortError" ? "The request took too long and was cancelled - please try again." : `Something went wrong: ${err.message}`;
    appendMessage("error", message);
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
    if (!sessionId) {
      await ensureSession();
    }
    const res = await fetchWithTimeout(
      `${API_BASE}/chat`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      },
      REQUEST_TIMEOUT_MS
    );
    removeTyping(typingRow);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();
    handleResponse(data);
  } catch (err) {
    removeTyping(typingRow);
    const message = err.name === "AbortError" ? "The request took too long and was cancelled - please try again." : `Something went wrong: ${err.message}`;
    appendMessage("error", message);
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
    await ensureSessionWithRetry();
    chatInput.focus();
  } catch (err) {
    appendMessage(
      "error",
      `Having trouble reaching the backend at ${API_BASE} (it may still be waking up - free-tier cold start). Go ahead and try sending a message anyway.`
    );
  } finally {
    setInputEnabled(true);
  }
})();
