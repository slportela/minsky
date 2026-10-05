"use client";

// Customer surface: trusted test-session gate, then conversation with the dispute assistant. A demo operator
// credential (ADR 0015) first chooses which customer to chat as; the chat itself is the same.
// Shows only what the backend returns; it never builds facts or decisions on its own.

import { FormEvent, useRef, useState } from "react";

import {
  ApiError,
  ChatMessage,
  ChatResponse,
  DemoSession,
  getDemoOperator,
  postChatTurn,
  postDemoSession,
} from "../../lib/api";

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
  const [operator, setOperator] = useState<{ credential: string; operatorId: string } | null>(null);
  const [actingAs, setActingAs] = useState<DemoSession | null>(null);
  const [customerChoice, setCustomerChoice] = useState("");
  const inFlight = useRef(false);

  async function continueWithCredential(event: FormEvent) {
    event.preventDefault();
    const id = credential.trim();
    if (!id) {
      setError("Indica tu credencial de prueba.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const operatorId = await getDemoOperator(id);
      if (operatorId) {
        setOperator({ credential: id, operatorId });
        return;
      }
      setLoggedIn(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setBusy(false);
    }
  }

  async function chooseCustomer(choice: { customerId: string } | { random: true }) {
    if (!operator || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const session = await postDemoSession(operator.credential, choice);
      startNewConversation();
      setActingAs(session);
      setCredential(session.credential);
      setLoggedIn(true);
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

  function changeCustomer() {
    if (inFlight.current || !operator) return;
    startNewConversation();
    setActingAs(null);
    setCredential(operator.credential);
    setLoggedIn(false);
  }

  function changeSession() {
    if (inFlight.current) return;
    startNewConversation();
    setCredential("");
    setOperator(null);
    setActingAs(null);
    setCustomerChoice("");
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

  if (!loggedIn && operator) {
    return (
      <main className="chat-page">
        <div className="gate card">
          <h1>Chat</h1>
          <p role="status" className="notice">
            Modo demo: la credencial <code>{operator.operatorId}</code> permite elegir con qué cliente se conversa.
            Es solo para la demostración; no es autenticación de clientes.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const id = customerChoice.trim();
              if (!id) {
                setError("Indica el id del cliente (por ejemplo CLI-…).");
                return;
              }
              void chooseCustomer({ customerId: id });
            }}
          >
            <label>
              Cliente{" "}
              <input
                value={customerChoice}
                onChange={(e) => setCustomerChoice(e.target.value)}
                autoComplete="off"
                placeholder="CLI-…"
                disabled={busy}
              />
            </label>{" "}
            <button type="submit" disabled={busy}>
              Elegir
            </button>{" "}
            <button type="button" disabled={busy} onClick={() => void chooseCustomer({ random: true })}>
              Uno al azar con cargos recientes
            </button>{" "}
            <button type="button" disabled={busy} onClick={changeSession}>
              Cambiar credencial
            </button>
          </form>
          {busy ? <p>Eligiendo…</p> : null}
          {error ? <p role="alert">{error}</p> : null}
        </div>
      </main>
    );
  }

  if (!loggedIn) {
    return (
      <main className="chat-page">
        <div className="gate card">
          <h1>Chat</h1>
          <p className="muted">Ingresa la credencial de prueba que te proporcionó el equipo.</p>
          <form onSubmit={(event) => void continueWithCredential(event)}>
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
            <button type="submit" disabled={busy}>
              Continuar
            </button>
          </form>
          {error ? <p role="alert">{error}</p> : null}
        </div>
      </main>
    );
  }

  return (
    <main className="chat-page">
      <div className="chat-head">
        <h1>Asistente de disputas</h1>
        <p>Cuéntame qué cargo no reconoces o no es correcto, y lo reviso contigo.</p>
      </div>
      {actingAs ? (
        <p role="status" className="notice">
          Modo demo · operador <code>{actingAs.operator_id}</code> · cliente <code>{actingAs.customer_id}</code>
          {actingAs.first_name ? ` (${actingAs.first_name}${actingAs.country ? `, ${actingAs.country}` : ""})` : ""} ·
          la sesión vence {new Date(actingAs.expires_at).toLocaleString("es")}.
        </p>
      ) : null}
      {actingAs ? (
        <details open className="card charges">
          <summary>Cargos recientes de este cliente (últimos 120 días)</summary>
          {actingAs.recent_charges === null ? (
            <p>No se pudieron leer los cargos; la sesión sigue siendo válida.</p>
          ) : actingAs.recent_charges.length === 0 ? (
            <p>Este cliente no tiene cargos en los últimos 120 días: cualquier reclamo sería fuera de plazo.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Comercio</th>
                  <th>Monto</th>
                  <th>Estado</th>
                  <th>Qué haría el sistema</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {actingAs.recent_charges.map((charge) => (
                  <tr key={charge.transaction_id}>
                    <td>{charge.date ?? "?"}</td>
                    <td>{charge.merchant ?? "(sin comercio)"}</td>
                    <td>
                      {charge.amount ?? charge.amount_usd} {charge.currency ?? "USD"}
                    </td>
                    <td>{charge.status ?? "?"}</td>
                    <td>
                      {charge.existing_dispute_id
                        ? `ya tiene el reclamo ${charge.existing_dispute_id}`
                        : (charge.hint ?? charge.rule_id)}{" "}
                      <small>({charge.rule_id})</small>
                    </td>
                    <td>
                      <button type="button" disabled={busy} onClick={() => setDraft(charge.suggested_message)}>
                        Usar
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </details>
      ) : null}
      <p className="toolbar">
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
        {actingAs ? (
          <>
            <button type="button" disabled={busy} onClick={changeCustomer}>
              Cambiar cliente
            </button>{" "}
          </>
        ) : null}
        <button type="button" disabled={busy} onClick={changeSession}>
          Cambiar sesión
        </button>
      </p>

      <section aria-live="polite" style={{ display: "grid", gap: "0.75rem", marginBottom: "1.5rem" }}>
        {messages.length === 0 && !busy ? <p className="empty">Escribe el cargo que quieres disputar.</p> : null}
        {messages.map((message, index) => (
          <div key={`${messageRole(message)}-${index}`} className={`msg ${messageRole(message)}`}>
            <span className="who">{messageRole(message) === "user" ? "Tú" : "Asistente"}</span>
            <div className="bubble">{messageText(message)}</div>
          </div>
        ))}
        {busy ? (
          <span className="typing" role="status" aria-label="Pensando…">
            <i />
            <i />
            <i />
          </span>
        ) : null}
      </section>

      <form onSubmit={onSubmit} className="composer">
        <label>
          Mensaje
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line; never while an IME composition is open.
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void sendText(draft, { clearDraft: true });
              }
            }}
            rows={2}
            disabled={busy}
          />
        </label>
        <div className="actions">
          <button type="submit" disabled={busy || !draft.trim()}>
            Enviar
          </button>
          <button type="button" disabled={busy} onClick={() => void sendText("sí")}>
            Sí
          </button>
          <button type="button" disabled={busy} onClick={() => void sendText("no")}>
            No
          </button>
          <span className="hint">Enter envía · Mayús+Enter salto de línea</span>
        </div>
      </form>
      {error ? <p role="alert">{error}</p> : null}
    </main>
  );
}
