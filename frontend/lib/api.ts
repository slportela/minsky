// All backend calls go through here. The browser calls same-origin /api/*: Caddy (local, demo)
// or the load balancer (prod) routes it to the backend, so there is no CORS and no API URL in the bundle.

export type UserMessage = { user: string };
export type AgentMessage = { agent: string };
export type ChatMessage = UserMessage | AgentMessage;

export type ChatRequest = {
  conversation_id?: string;
  messages: ChatMessage[];
};

export type ChatResponse = {
  conversation_id: string;
  messages: ChatMessage[];
};

export type ErrorResponse = {
  code: string;
  message: string;
  request_id: string;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly requestId?: string;

  constructor(status: number, message: string, code?: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let code: string | undefined;
    let message = `${init?.method ?? "GET"} /api${path} failed: ${response.status}`;
    let requestId: string | undefined;
    try {
      const body = (await response.json()) as Partial<ErrorResponse>;
      if (typeof body.message === "string" && body.message.trim()) {
        message = body.message;
      }
      if (typeof body.code === "string") {
        code = body.code;
      }
      if (typeof body.request_id === "string") {
        requestId = body.request_id;
      }
    } catch {
      // non-JSON error body: keep the status fallback message
    }
    throw new ApiError(response.status, message, code, requestId);
  }
  return response.json() as Promise<T>;
}

const CUSTOMER_HEADER = "X-Minsky-Customer-Id";

export async function postChatTurn(args: {
  customerId: string;
  conversationId?: string;
  messages: ChatMessage[];
}): Promise<ChatResponse> {
  const body: ChatRequest = {
    messages: args.messages,
    ...(args.conversationId ? { conversation_id: args.conversationId } : {}),
  };
  return api<ChatResponse>("/chat/turn", {
    method: "POST",
    headers: { [CUSTOMER_HEADER]: args.customerId },
    body: JSON.stringify(body),
  });
}
