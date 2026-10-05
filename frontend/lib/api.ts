// All backend calls go through here. The browser calls same-origin /api/*: Caddy (local, demo)
// or the load balancer (prod) routes it to the backend, so there is no CORS and no API URL in the bundle.

export type UserMessage = { user: string };
export type AgentMessage = { agent: string };
export type ChatMessage = UserMessage | AgentMessage;

// The flow a conversation runs: "workflow" (extract, then search) or "agentic" (a tool-using agent searches).
export type ChatMode = "workflow" | "agentic";

// What the server allows. The chat offers the choice only when `mode_switch` is true; `mode` is the server default.
export type ChatOptions = { mode_switch: boolean; mode: ChatMode };

export type ChatRequest = {
  conversation_id?: string;
  messages: ChatMessage[];
  // Only read on the first turn of a conversation, and only when the server allows the switch.
  mode?: ChatMode;
};

export type ChatResponse = {
  conversation_id: string;
  messages: ChatMessage[];
  mode: ChatMode; // the flow this conversation runs, fixed when it started
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

export async function getChatOptions(): Promise<ChatOptions> {
  return api<ChatOptions>("/chat/options");
}

export async function postChatTurn(args: {
  credential: string;
  conversationId?: string;
  messages: ChatMessage[];
  mode?: ChatMode;
}): Promise<ChatResponse> {
  const body: ChatRequest = {
    messages: args.messages,
    ...(args.conversationId ? { conversation_id: args.conversationId } : {}),
    // The flow is chosen when a conversation starts: a later turn never carries it.
    ...(!args.conversationId && args.mode ? { mode: args.mode } : {}),
  };
  return api<ChatResponse>("/chat/turn", {
    method: "POST",
    headers: { Authorization: `Bearer ${args.credential}` },
    body: JSON.stringify(body),
  });
}

// ---- Demo operator (ADR 0015): a credential that chooses which customer to chat as ----

export type DemoSession = {
  credential: string;
  customer_id: string;
  first_name: string | null;
  country: string | null;
  operator_id: string;
  expires_at: string;
};

// Is this credential a demo operator's? Null when it is not, or when the demo mode is off (404 or 401): the page
// then treats it as an ordinary customer credential.
export async function getDemoOperator(credential: string): Promise<string | null> {
  try {
    const body = await api<{ operator_id: string }>("/demo/whoami", {
      headers: { Authorization: `Bearer ${credential}` },
    });
    return body.operator_id;
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 401)) {
      return null;
    }
    throw error;
  }
}

export async function postDemoSession(
  credential: string,
  choice: { customerId: string } | { random: true },
): Promise<DemoSession> {
  const body = "random" in choice ? { random: true } : { customer_id: choice.customerId };
  return api<DemoSession>("/demo/session", {
    method: "POST",
    headers: { Authorization: `Bearer ${credential}` },
    body: JSON.stringify(body),
  });
}

// ---- Agent console (staff credential; never a customer one) ----

export type CaseSummary = {
  case_id: string;
  kind: "dispute" | "handoff";
  priority: "Critical" | "High" | "Medium" | "Low";
  queue: "fraud" | "disputes" | "general";
  status: "new" | "in_progress" | "resolved";
  summary: string;
  rule_id: string | null;
  due_at: string;
  overdue: boolean;
  assigned_to: string | null;
  created_at: string;
  amount_usd: string | null;
  merchant: string | null;
};

export type QueueStats = {
  open_cases: number;
  overdue: number;
  by_priority: Record<string, number>;
  by_queue: Record<string, number>;
};

export type CaseList = { agent_id: string; stats: QueueStats; cases: CaseSummary[] };

export type AuditEntry = { tool: string; outcome: string; reason: string | null; at: string };

export type CaseDetail = {
  case: CaseSummary;
  customer_id: string;
  reason: string;
  triage_reason: string;
  facts: {
    verified?: Record<string, unknown>;
    // Read by code from the bank and the policy (customer profile, the rule, the search); agentic mode only.
    context?: Record<string, unknown>;
    customer_said?: Record<string, unknown>;
  };
  actions: string[];
  open_questions: string[];
  expected_resolution_days: number | null;
  resolution_note: string | null;
  audit: AuditEntry[];
};

function staffHeaders(credential: string): Record<string, string> {
  return { Authorization: `Bearer ${credential}` };
}

export async function listCases(credential: string, queue?: string): Promise<CaseList> {
  const query = queue ? `?queue=${encodeURIComponent(queue)}` : "";
  return api<CaseList>(`/console/cases${query}`, { headers: staffHeaders(credential) });
}

export async function getCase(credential: string, caseId: string): Promise<CaseDetail> {
  return api<CaseDetail>(`/console/cases/${encodeURIComponent(caseId)}`, { headers: staffHeaders(credential) });
}

export async function claimCase(credential: string, caseId: string): Promise<CaseDetail> {
  return api<CaseDetail>(`/console/cases/${encodeURIComponent(caseId)}/claim`, {
    method: "POST",
    headers: staffHeaders(credential),
  });
}

export async function resolveCase(credential: string, caseId: string, note: string): Promise<CaseDetail> {
  return api<CaseDetail>(`/console/cases/${encodeURIComponent(caseId)}/resolve`, {
    method: "POST",
    headers: staffHeaders(credential),
    body: JSON.stringify({ note }),
  });
}
