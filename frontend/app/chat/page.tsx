"use client";

// Customer surface: trusted test-session gate, then conversation with the dispute assistant.
// Shows only what the backend returns; it never builds facts or decisions on its own.

import { FormEvent, useRef, useState } from "react";

import { ApiError, ChatMessage, ChatResponse, postChatTurn } from "../../lib/api";

function messageText(message: ChatMessage): string {
  return "user" in message ? message.user : message.agent;
}

function messageRole(message: ChatMessage): "user" | "agent" {
  return "user" in message ? "user" : "agent";
}

function assertChatResponse(value: ChatResponse): ChatResponse {
  if (typeof value.conversation_id !== "string" || !value.conversation_id) {
    throw new Error("Respuesta inválida: falta conversation_id");
  }
  if (!Array.isArray(value.messages)) {
    throw new Error("Respuesta inválida: messages no es una lista");
  }
  for (const message of value.messages) {
    if (message == null || typeof message !== "object") {
      throw new Error("Respuesta inválida: mensaje mal formado");
    }
    const hasUser = "user" in message && typeof message.user === "string";
    const hasAgent = "agent" in message && typeof message.agent === "string";
    if (hasUser === hasAgent) {
      throw new Error("Respuesta inválida: cada mensaje debe ser user o agent");
    }
  }
  return value;
}

export default function ChatPage() {
  const [credential, setCredential] = useState("");
  const [loggedIn, setLoggedIn] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  function continueWithCredential(event: FormEvent) {
    event.preventDefault();
    const id = credential.trim();
    if (!id) {
      setError("Indica tu credencial de prueba.");
      return;
    }
    setError(null);
    setLoggedIn(true);
  }

  function changeSession() {
    if (inFlight.current) return;
    startNewConversation();
    setCredential("");
    setLoggedIn(false);
  }

  function startNewConversation() {
    if (inFlight.current) {
      return;
    }
    setConversationId(undefined);
    setMessages([]);
    setDraft("");
    setError(null);
  }

  async function sendText(text: string, opts?: { clearDraft?: boolean }) {
    const trimmed = text.trim();
    if (!trimmed || inFlight.current || !loggedIn) {
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const response = assertChatResponse(
        await postChatTurn({
          credential: credential.trim(),
          conversationId,
          messages: [...messages, { user: trimmed }],
        }),
      );
      setConversationId(response.conversation_id);
      setMessages(response.messages);
      if (opts?.clearDraft) {
        setDraft("");
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.code ? `${err.code}: ${err.message}` : err.message);
      } else {
        setError(err instanceof Error ? err.message : "Error desconocido");
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void sendText(draft, { clearDraft: true });
  }

  if (!loggedIn) {
    return (
      <main>
        <h1>Chat</h1>
        <p>Ingresa la credencial de prueba que te proporcionó el equipo.</p>
        <form onSubmit={continueWithCredential}>
          <label>
            Credencial de prueba{" "}
            <input
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
              type="password"
              autoComplete="off"
              disabled={busy}
            />
          </label>{" "}
          <button type="submit">Continuar</button>
        </form>
        {error ? <p role="alert">{error}</p> : null}
      </main>
    );
  }

  return (
    <main>
      <h1>Chat</h1>
      <p>
        Sesión de prueba
        {conversationId ? (
          <>
            {" "}
            · conversación <code>{conversationId}</code>
          </>
        ) : null}{" "}
        <button type="button" disabled={busy} onClick={startNewConversation}>
          Nueva conversación
        </button>{" "}
        <button type="button" disabled={busy} onClick={changeSession}>
          Cambiar sesión
        </button>
      </p>

      <section aria-live="polite" style={{ display: "grid", gap: "0.75rem", marginBottom: "1.5rem" }}>
        {messages.length === 0 ? <p>Escribe el cargo que quieres disputar.</p> : null}
        {messages.map((message, index) => (
          <div key={`${messageRole(message)}-${index}`}>
            <strong>{messageRole(message) === "user" ? "Tú" : "Asistente"}</strong>
            <div style={{ whiteSpace: "pre-wrap" }}>{messageText(message)}</div>
          </div>
        ))}
      </section>

      <form onSubmit={onSubmit} style={{ display: "grid", gap: "0.5rem", maxWidth: "40rem" }}>
        <label>
          Mensaje
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            disabled={busy}
            style={{ display: "block", width: "100%" }}
          />
        </label>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <button type="submit" disabled={busy || !draft.trim()}>
            Enviar
          </button>
          <button type="button" disabled={busy} onClick={() => void sendText("sí")}>
            Sí
          </button>
          <button type="button" disabled={busy} onClick={() => void sendText("no")}>
            No
          </button>
        </div>
      </form>
      {busy ? <p>Pensando…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
    </main>
  );
}
