"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { createSession, getApiBase, postChat, postConfirm } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";

const REQUEST_TIMEOUT_MS = 100000; // longer than the backend's own worst-case retry/fallback time
const SEND_COOLDOWN_MS = 3000; // guards against rapid messages exhausting the
// shared Groq per-minute token budget - a burst of quick messages is what
// actually triggered the rate-limit cascade during testing, not normal use
const DEFAULT_PLACEHOLDER = "e.g. something for an outdoor September wedding, under $200";
const WELCOME_TEXT =
  "Hi! Tell me what you're shopping for - an occasion, a budget, colours to avoid - and I'll search the catalog for you.";

type NewItem =
  | { kind: "message"; role: "user" | "assistant" | "error"; text: string; markdown?: boolean }
  | { kind: "confirm"; tool: string; args: Record<string, unknown>; resolved: boolean }
  | { kind: "typing"; slow: boolean };

type TimelineItem = NewItem & { id: number };

export default function Home() {
  const [items, setItems] = useState<TimelineItem[]>([{ id: 0, kind: "message", role: "assistant", text: WELCOME_TEXT }]);
  const [awaitingConfirmation, setAwaitingConfirmation] = useState(false);
  const [inputEnabled, setInputEnabledState] = useState(false);
  const [placeholder, setPlaceholder] = useState("Connecting... (may take up to 30s on a cold start)");
  const [sendLabel, setSendLabel] = useState("Send");
  const [inputValue, setInputValue] = useState("");

  const apiBaseRef = useRef<string>("");
  const sessionIdRef = useRef<string | null>(null);
  const nextId = useRef(1);
  const chatWindowRef = useRef<HTMLElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const cooldownInterval = useRef<ReturnType<typeof setInterval> | null>(null);
  const typingSlowTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function scrollToBottom() {
    const el = chatWindowRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }

  useEffect(scrollToBottom, [items]);

  function addItem(item: NewItem): number {
    const id = nextId.current++;
    setItems((prev) => [...prev, { ...item, id }]);
    return id;
  }

  function removeItem(id: number) {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }

  function setInputEnabled(enabled: boolean, waitingText?: string) {
    setInputEnabledState(enabled);
    setPlaceholder(enabled ? DEFAULT_PLACEHOLDER : waitingText || "Waiting for a reply...");
  }

  function appendTyping(): number {
    const id = addItem({ kind: "typing", slow: false });
    typingSlowTimer.current = setTimeout(() => {
      setItems((prev) => prev.map((it) => (it.id === id && it.kind === "typing" ? { ...it, slow: true } : it)));
    }, 12000);
    return id;
  }

  function removeTyping(id: number) {
    if (typingSlowTimer.current) clearTimeout(typingSlowTimer.current);
    removeItem(id);
  }

  function startSendCooldown() {
    let remaining = Math.ceil(SEND_COOLDOWN_MS / 1000);
    setInputEnabled(false);
    setSendLabel(`Wait ${remaining}s`);
    cooldownInterval.current = setInterval(() => {
      remaining -= 1;
      if (remaining > 0) {
        setSendLabel(`Wait ${remaining}s`);
      } else {
        if (cooldownInterval.current) clearInterval(cooldownInterval.current);
        setSendLabel("Send");
        setInputEnabled(true);
        inputRef.current?.focus();
      }
    }, 1000);
  }

  async function ensureSession(): Promise<string> {
    const stored = sessionStorage.getItem("fashion_mcp_session_id");
    if (stored) {
      sessionIdRef.current = stored;
      return stored;
    }
    const id = await createSession(apiBaseRef.current);
    sessionStorage.setItem("fashion_mcp_session_id", id);
    sessionIdRef.current = id;
    return id;
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
    sessionIdRef.current = null;
    setAwaitingConfirmation(false);
    setInputEnabled(true);
    nextId.current = 1;
    setItems([{ id: 0, kind: "message", role: "assistant", text: WELCOME_TEXT }]);
    try {
      await ensureSession();
    } catch (err) {
      addItem({ kind: "message", role: "error", text: `Couldn't reach the backend: ${(err as Error).message}` });
    }
  }

  function handleResponse(data: Awaited<ReturnType<typeof postChat>>) {
    if (data.type === "confirmation_required") {
      setAwaitingConfirmation(true);
      addItem({ kind: "confirm", tool: data.tool, args: data.args, resolved: false });
      setInputEnabled(false, "Respond to the confirmation above first...");
      return true;
    }
    addItem({ kind: "message", role: "assistant", text: data.text, markdown: true });
    return false;
  }

  async function resolveConfirmation(confirmId: number, answer: "yes" | "no") {
    setItems((prev) => prev.map((it) => (it.id === confirmId && it.kind === "confirm" ? { ...it, resolved: true } : it)));
    const typingId = appendTyping();
    try {
      const sid = sessionIdRef.current ?? (await ensureSession());
      const data = await postConfirm(apiBaseRef.current, sid, answer, REQUEST_TIMEOUT_MS);
      removeTyping(typingId);
      setAwaitingConfirmation(false);
      const hitConfirmation = handleResponse(data);
      if (!hitConfirmation) startSendCooldown();
    } catch (err) {
      removeTyping(typingId);
      const message =
        (err as Error).name === "AbortError"
          ? "The request took too long and was cancelled - please try again."
          : `Something went wrong: ${(err as Error).message}`;
      addItem({ kind: "message", role: "error", text: message });
      setAwaitingConfirmation(false);
      startSendCooldown();
    }
  }

  async function sendMessage(text: string) {
    addItem({ kind: "message", role: "user", text });
    const typingId = appendTyping();
    setInputEnabled(false);
    let hitConfirmation = false;
    try {
      const sid = sessionIdRef.current ?? (await ensureSession());
      const data = await postChat(apiBaseRef.current, sid, text, REQUEST_TIMEOUT_MS);
      removeTyping(typingId);
      hitConfirmation = handleResponse(data);
    } catch (err) {
      removeTyping(typingId);
      const message =
        (err as Error).name === "AbortError"
          ? "The request took too long and was cancelled - please try again."
          : `Something went wrong: ${(err as Error).message}`;
      addItem({ kind: "message", role: "error", text: message });
    } finally {
      if (!hitConfirmation) startSendCooldown();
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = inputValue.trim();
    if (!text || awaitingConfirmation) return;
    setInputValue("");
    sendMessage(text);
  }

  useEffect(() => {
    apiBaseRef.current = getApiBase();
    (async () => {
      try {
        await ensureSessionWithRetry();
        inputRef.current?.focus();
        setInputEnabled(true);
      } catch {
        addItem({
          kind: "message",
          role: "error",
          text: `Having trouble reaching the backend at ${apiBaseRef.current} (it may still be waking up - free-tier cold start). Go ahead and try sending a message anyway.`,
        });
        setInputEnabled(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            &#10022;
          </span>
          <div className="brand-text">
            <h1>Fashion Stylist</h1>
            <p className="brand-sub">Real catalog. Real prices. No guessing.</p>
          </div>
        </div>
        <button id="new-session-btn" title="Start a new conversation" onClick={() => startNewSession()}>
          New chat
        </button>
      </header>

      <main id="chat-window" className="chat-window" aria-live="polite" ref={chatWindowRef as never}>
        {items.map((item) => {
          if (item.kind === "typing") {
            return (
              <div className="message assistant typing" key={item.id}>
                <div className="bubble">
                  {item.slow ? (
                    "Still working - this can take up to a minute under heavy load..."
                  ) : (
                    <span className="typing-dots">
                      <span />
                      <span />
                      <span />
                    </span>
                  )}
                </div>
              </div>
            );
          }
          if (item.kind === "confirm") {
            return (
              <div className="message assistant" key={item.id}>
                <div className="confirm-card">
                  <div className="confirm-title">Confirm action: {item.tool}</div>
                  <pre>{JSON.stringify(item.args, null, 2)}</pre>
                  <div className="confirm-actions">
                    <button
                      className="confirm-yes"
                      disabled={item.resolved}
                      onClick={() => resolveConfirmation(item.id, "yes")}
                    >
                      Yes, do it
                    </button>
                    <button
                      className="confirm-no"
                      disabled={item.resolved}
                      onClick={() => resolveConfirmation(item.id, "no")}
                    >
                      No, cancel
                    </button>
                  </div>
                </div>
              </div>
            );
          }
          return (
            <div className={`message ${item.role}`} key={item.id}>
              {item.markdown ? (
                <div className="bubble" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.text) }} />
              ) : (
                <div className="bubble">{item.text}</div>
              )}
            </div>
          );
        })}
      </main>

      <form id="chat-form" className="chat-form" onSubmit={handleSubmit}>
        <input
          id="chat-input"
          type="text"
          autoComplete="off"
          placeholder={placeholder}
          aria-label="Message"
          disabled={!inputEnabled}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          ref={inputRef}
        />
        <button type="submit" id="send-btn" disabled={!inputEnabled}>
          {sendLabel}
        </button>
      </form>
    </div>
  );
}
