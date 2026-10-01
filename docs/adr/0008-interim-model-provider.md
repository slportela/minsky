# 0008. Interim model provider: the OpenAI API with GPT-6 Luna while Bedrock is blocked

- Status: accepted (decided by Sebastián on 2026-09-30; temporary, see "When it ends")
- Date: 2026-09-30
- Partially supersedes: [0001](0001-aws-only.md), only its "models through Amazon Bedrock only" point, and only until Bedrock works for our account

## Context
- ADR 0001 routes every model call through Amazon Bedrock. Our AWS account cannot use it yet: a test call to both Claude Haiku 4.5 and gpt-oss-120b returns *"Access to Bedrock models is not allowed for this account"* (2026-09-30). The block is account-wide, not a missing model agreement, and its fix (account plan or support case) has no known lead time.
- The submission is due 2026-10-05, and the orchestrator, the eval simulator and the demo all need a model.
- **GPT-6 Luna** (OpenAI, released 2026-09-22) is available both on the OpenAI API (`gpt-6-luna`) and on Bedrock (`us.openai.gpt-6-luna`, through Bedrock's OpenAI-compatible `/openai/v1` endpoint). It is the fast, low-cost tier of GPT-6 (about USD 0.10-0.11 per million input tokens and 0.50-0.55 per million output tokens), with structured outputs and adjustable reasoning effort.
- A team member has OpenAI API credit available now.
- The brief allows any tools; it only forbids sending private customer records or restricted data to external model requests. Our data is synthetic.

## Decision
- The backend calls the model through **one interface over the OpenAI-compatible Responses API** (`backend/src/minsky_api/llm/client.py`). Base URL, model id and key are settings (`MINSKY_LLM_BASE_URL`, `MINSKY_LLM_MODEL`, `MINSKY_LLM_API_KEY`).
- **Interim provider: the OpenAI API with `gpt-6-luna`**, using a dedicated OpenAI project key with a budget limit.
- The model id is pinned and checked on every response; retries are bounded; calls send `store: false`; token usage and latency are recorded for traces and eval reports.
- What goes to the model stays minimal: the conversation and the verified facts a step needs, never full customer records. The read models already exclude identity documents, contact details, income and credit score (`docs/read_models.md`).

## When it ends
As soon as Bedrock works for our account, switch the settings to Bedrock with the **same model** (`MINSKY_LLM_BASE_URL=https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1`, `MINSKY_LLM_MODEL=us.openai.gpt-6-luna`) and mark this ADR superseded. No code change is expected; rerun the eval smoke suite to confirm the results match.

## Consequences
- The demo and the evals are unblocked now, at a low cost per call.
- For as long as this lasts, model calls leave AWS: the data-residency and "one cloud" arguments of ADR 0001 do not hold, and the submission must say so (reported limitation).
- The OpenAI API is a SaaS dependency; its rate limits and outages become ours. Bounded retries and the "degrade to a human" rule (`docs/architecture.md`) cover failures.
- Keeping the same model for the move to Bedrock keeps eval results comparable across providers; a different model would need a new eval run.
- The credit belongs to one person: the key must not be committed, shared in chat or baked into images; each environment reads it from its own `.env` or secret store.
