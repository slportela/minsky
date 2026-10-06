# Agentic dispute search (exploration)

> **Status: implemented behind `MINSKY_AGENT_MODE=agentic` (default `workflow`); unit-tested, not yet evaluated, not adopted.** Section 11 says what was built and what was not verified. It proposes replacing the extract-then-search loop with a tool-using agent that finds the transaction through conversation. It knowingly departs from parts of `docs/solution.md` and ADR 0003; section 6 lists exactly which. If it is adopted it needs an ADR that supersedes those parts, and an eval delta (AGENTS.md rule 3).

## 1. Why

Today the model only turns one message into filters (`agent.extract.j2`) and the code searches with exact amount, exact dates and a literal merchant `ILIKE`. A customer who says "I don't recognise a charge of 123 USD" gets "no match" when the charge is 123.10; "yesterday, around 80" finds nothing when it was 83 today. Every flexibility rule we add (near amount, date window, fuzzy merchant, type/category, currency) is another hand-written branch.

An agent that can look at the customer's own transactions, query them as it likes, and talk to the customer to narrow down gets that flexibility from the model, while the dangerous part (is it allowed, did the customer say yes, what happened) stays in code.

## Choosing the flow

| Where | How |
|---|---|
| **Server default** | `MINSKY_AGENT_MODE=workflow` (default) or `agentic`. Read once per process (`get_settings` is cached): recreate the API container to change it. `compose.yaml` passes it through. |
| **From the chat UI** | Off by default. `MINSKY_ALLOW_MODE_SWITCH=true` makes `GET /api/chat/options` answer `mode_switch: true`, and the chat page shows a "Flujo" selector. The choice travels as `mode` on the **first** turn of a conversation; later turns never carry it. |
| **Evals** | `--agent-mode` (`EVAL_AGENT_MODE` in the Makefile). |

A conversation keeps the flow it started with. Changing the selector starts a new conversation. With the switch off the backend ignores a `mode` sent by hand, so a customer cannot choose between an evaluated flow and an experimental one. The flow is not a permission: both apply the same policy and the same tools, and the customer always comes from the credential. The response carries `mode`, and the page shows the one in progress. There is no per-customer or percentage routing.

## 2. Flow

```
 customer ──▶ SEARCH (agent loop) ──────────────┐   the only phase where a model is free
              tools: query_transactions          │
                     propose_transaction(id) ────┘
                                   │ id ∈ rows this conversation actually returned, and owned
                                   ▼
              CONFIRM (programmatic)   card of VERIFIED facts re-read by id + "dispute this? yes/no"
                 no ──▶ back to SEARCH; the code tells the agent "customer rejected <id>"
                 yes ─▶ policy.decide on bank facts (D01-D09)
                                   │
                  allowed (D09) ───┴─── denied (D01-D08)
                       │                      │
               open_dispute + read-back      DENIAL (programmatic): reason from the rule,
               reference + expected time     then "escalate to an agent? yes/no"
                                                   │ yes
                                                   ▼
                                       ESCALATE: create_handoff with the summary (section 5)
```

The agent never opens, blocks or escalates. It has no tool for that. It ends SEARCH by proposing one id, or by saying it cannot find it (then the code offers escalation with the same summary).

## 3. The agent

**Tools (typed, session-scoped, audited like every tool in `tools/bank.py`):**

| Tool | Does | Guard |
|---|---|---|
| `query_transactions(sql)` | Read-only query over the customer's own data (section 4) | Sandbox, row cap, timeout; the audit record keeps a digest of the SQL and the row count, the trace span keeps the SQL text (first 300 characters) |
| `propose_transaction(transaction_id, customer_says_not_me, note?)` | Ends SEARCH. The optional `note` is one sentence on how the transaction differs from what the customer said; it passes the reply checks and is shown before the code-written card | Rejected unless the id was returned by an earlier query in this conversation and belongs to the session customer |

**Context.** Unlike the extractor, the agent receives the whole conversation plus its tool calls and results. Rows from the database (merchant names, free text) are untrusted data, not instructions; the prompt says so and an eval case injects text through a merchant name.

**Prompt.** `prompts/agent.search.j2`, static and cacheable: the goal ("find the one transaction the customer wants to dispute; do not decide whether it can be disputed; never say a dispute was opened, a card blocked or a case escalated"), the schema of the view, how to ask for a missing detail, how to treat approximate amounts and relative dates, and the customer's language. "Today" is passed as the one per-request variable, after the static part.

**Limits.** At most 5 tool calls per customer message, at most N customer turns in SEARCH (reuse `max_turns`), at most 20 rows per query. When exhausted, the code offers escalation.

**Output text.** Everything the agent says goes through the checks that already exist in `agent/speak.py`: `action_claims` (no completed-action claims without facts), `ungrounded_number` (every amount and date in the text must come from a tool result or from the customer) and the language check. A refused text is retried a bounded number of times and then falls back to a code template.

### Closing the conversation

The agentic mode closes like the workflow does (`main`: terminal state, `wording.terminal_reply`): once a conversation ends, every later message gets a code-written status, the reference and "start a new conversation", with no model and no tool call. What the customer reads when the search ends is written by code (`agent/agentic_wording.py`, es and pt), from what was verified; the agent never writes it.

- **Nothing reaches a person before the customer confirms a charge or accepts a person** (`txn_confirmed`, `handoff_accepted`; `_require_confirmed_case` fails loudly otherwise). At the turn limit with neither, the conversation ends without a case.
- **Offer of a person** (nothing found, not a dispute, or too many replies that are not yes or no): says it could not find the transaction and is sorry, then asks yes or no. `give_up(not_found)` is refused until the agent has asked the customer something. Offers are bounded (`max_handoff_offers`): a no goes back to the search once ("no te paso con nadie, dime el monto, el comercio o la fecha"), and after the last offer the conversation ends without a case.
- **Yes:** says a person will take the case, that they already have the summary so nothing needs repeating, and the real reference (read back from the case queue). A card block is said first only if it was read back.
- **No after a policy denial:** the conversation ends ("informed": no new dispute opened, and the existing one if there is one).
- **Replies that are neither yes nor no:** a plain yes or no is the only consent (`agent.consent`); anything else gets the question again with "only yes or no works here" (`with_only_yes_no`). At `max_unclear_replies` the conversation goes to a person as in the workflow: before the charge is confirmed a person is offered, while an offer is open it ends without a case, after the charge is confirmed the case goes to the queue with the unanswered question. A free-text reply to the card goes to the agent (a correction is heard) but counts toward the same limit. If the offer was "I could not find it", other words are new information and go back to the agent.
- **After a case exists** the terminal reply says so with the real reference (`Terminal.reference`).
- The shared helpers (`_finish`, `_ask`, `_consent`, the guard, `terminal_reply`) are the workflow's own; this mode does not keep a second set. A first version of this mode reopened the search when the customer wrote after declining; `main` decided on a terminal state, and this mode follows it.
- Portuguese was written for this and has no source data or reviewer (a stated limitation of the project).

## 4. Query sandbox

The customer's whole history is small: **median 29 transactions, p99 88, max 150** over the three years (134,515 customers, 4.4M transactions; 120-day window: median 3, p99 12). So isolation can be by construction instead of by filtering:

1. On the first SEARCH turn the code reads the session customer's rows through `TransactionStore` (all statuses, all dates, so older charges are found and the policy, not the search, refuses them with D05).
2. It loads them into a per-conversation **in-memory SQLite** database (standard library, no new dependency) as a view `transactions`. The data of other customers is not in that database, so no query can reach it.
3. The agent's SQL runs with `sqlite3.Connection.set_authorizer` allowing only `SELECT` on that view and a short list of functions; `set_progress_handler` for a time limit; a single statement; `PRAGMA query_only`; no `ATTACH`, no `sqlite_master`.
4. **Columns the model never sees:** `is_fraud`, `fraud_score`, `response_code`, `customer_id`. The fraud flag leaks the label (`docs/known_issues.md`) and AGENTS.md keeps it away from the model; the policy reads it from the bank row.
5. Facts shown to the customer are re-read from `bank.transactions` by id (read-back), never taken from the query result.

Alternative considered: a typed filter tool (`amount_between`, `date_between`, `merchant_like`, ...). Safer and easier to audit, but it brings back the rule-by-rule flexibility this exploration wants to avoid. It stays the fallback if the SQL sandbox proves hard to eval.

## 5. Escalation summary

Created only when the customer says yes to escalating (or search was exhausted). Two parts, kept apart so a reviewer can tell verified from narrated:

| Part | Source | Content |
|---|---|---|
| **Verified** | Code, from tools and the policy | Customer id; segment, country, tenure, products, complaint counts and last-90-day complaints; the transaction (id, date, amount, currency, merchant, status); the rule id and the denial reason; prior dispute reference if any; the flag `customer_says_not_me`; the fraud flag (for the human, never for the model) |
| **Narrated** | LLM from the transcript | What the customer said, what was searched, why the customer disagreed with the denial. Labelled "customer-reported, unverified". It runs through the same grounding checks |

This replaces `facts={"transaction_id", "route"}` in `_handoff` with a typed `HandoffSummary` (Pydantic, per AGENTS.md); `create_handoff` still reads back what it stored. Handoffs carry no secrets or full card numbers.

## 6. What this contradicts, and what it keeps

| Point | Today | In this design |
|---|---|---|
| `solution.md`: "Transaction search: code, exact, auditable" | SQL built by code | **Departs.** SQL written by the model, run in a sandbox. Auditability moves to logging every query and its row count |
| ADR 0003: "LLM understands and phrases; code decides and acts" | Extract-only model | **Partly departs.** The model also drives a search loop. Deciding and acting stay in code |
| Extractor sees one message, no history | Cheap, injection-resistant | **Departs.** Whole conversation in context; mitigated by data-not-instructions, the sandbox and output checks |
| Orchestrator: "never dump the customer's latest N rows" | Requires a narrowing signal | **Relaxed.** The data is the customer's own and capped at 20 rows per query; a "show me my last purchases" request becomes possible |
| Rule 1: the model never authorizes | Policy and permissions in code | **Kept.** The agent has no write tool; `open_dispute` still enforces D01-D09 itself |
| Rule 2: report only verified actions | Read-back + claim checks | **Kept.** Same checks on every agent text |
| Rule 3: evals gate behavior | | **Applies in full:** section 8 |
| Explicit consent for writes (`agent/consent.py`) | `explicit_yes` / `explicit_no` | **Kept.** Only these tokens move CONFIRM and the escalation offer; an unclear reply is classified (`confirm.py`) and never acts |
| Degraded mode, language detection | `degraded.py`, `language.py` | **Kept.** A provider failure during SEARCH is a handoff, like today |
| The fraud flag never reaches the model | | **Kept** (column hidden, section 4) |

## 7. Risks

| Risk | Mitigation |
|---|---|
| Agent proposes a wrong but plausible transaction | CONFIRM shows verified facts; "no" returns to SEARCH and the id is excluded; wrong proposals are an eval metric |
| Agent says "I opened your dispute" | It has no tool and no knowledge of later steps; `action_claims` check; `unverified_action_claim` already in `must_not` |
| Prompt injection through a merchant name or the customer's text | Rows are data; sandbox has no side effects; no write tool; eval case |
| Escape from the sandbox | Isolation by construction (only that customer's rows exist in the database) plus authorizer; denial tests for `ATTACH`, `sqlite_master`, multiple statements, `load_extension`, pragmas, giant joins |
| `customer_says_not_me` misread, so D06 and the card-block offer are skipped | CONFIRM asks "do you recognise this charge?" in code instead of trusting the model; open decision 2 |
| Cost and latency: several calls per turn with full history | Small model for SEARCH; cap on tool calls; measure cost per resolution in the eval run |
| Non-determinism makes evals noisy | Several trials per case (already the τ²-style method of ADR 0002); report with intervals |

## 8. Evals

- **A/B on the same dev cases**: the current workflow (baseline) against the agentic mode, switched by a setting, so the delta is real and rule 3 is satisfied. The old orchestrator stays.
- **New dev cases** (`/new-eval-case`), both directions (written: the `dispute-flex-*` cases, see `evals/README.md`; the rest are still to do): amount off by cents; "around 80, yesterday" with 83 today; merchant misspelt; charge with no merchant ("a withdrawal"); amount in COP vs USD; two same-day charges; nothing matches (escalates with summary); denial by D05/D07 then escalation; customer rejects the proposed transaction; query injection; injected merchant name; another customer's transaction id in the chat.
- **New checks**: no query result contains another customer's id (by construction, but tested); the agent never proposes an id it did not see; the escalation summary contains the rule id and every verified field; narrated part contains no ungrounded number.
- Metrics: resolution rate, wrong-transaction proposals, turns to resolution, cost per resolution, tool calls per turn.
- The test split stays locked; iterate on `dev`, select on `val`.

## 9. Decisions (as implemented)

1. **One "yes" for the dispute.** The card shows the verified facts and asks "should I open a claim for this?". That yes authorizes `open_dispute`; no second confirmation. A separate recognition question (2) comes before the policy runs.
2. **`customer_says_not_me` is asked in code.** After the yes, code asks "do you recognise this transaction?" and a "no" sets the flag (and the dispute reason `unrecognized_charge`). The question is skipped when the agent already heard that it was not the customer, which stays a hint the customer can only strengthen. A bank fraud flag routes to fraud (D06) whatever the customer says.
3. **SQL in the sandbox**, typed filters not built (section 4).
4. **Summary content:** the profile has segment, country, status, product counts and complaint counts, no name and no contact data; the transaction, the rule and the exact text the customer was told; how many queries ran and which proposals the customer rejected; one narrated paragraph. The bank fraud flag is not copied: rule D06 already says it, and the flag stays out of anything a model touches.
5. **A second mode behind a setting**, fixed per conversation when it starts (`ConversationState.mode`), so flipping the setting never lands a running conversation in the other mode's phases. The workflow orchestrator is untouched.
6. **When SEARCH gives up:** a message that uses its budget (5 tool calls) or fails the text checks twice gets a code-written question; the third such message offers a person. The agent can also give up itself (`not_found`, `out_of_scope`).

Where the doc and the policy meet, one case differs from the denial flow: a fraud route on something that is not a card (a transfer) goes straight to the fraud team, as in the workflow, because there is nothing to block and the case is urgent. Every other rule that is not D09 gets the denial message and the offer of a person.

## 10. Build order

1. ✅ `LLM.step`: tool calling over the Responses API, one call at a time, no provider ids, cut-off replies refused (`backend/tests/test_llm_client.py`).
2. ✅ `agent/sandbox.py` and `tools/history.py` (`load_history`, `query_transactions`, `get_customer_profile`), with the denial tests (`test_agent_sandbox.py`, `test_agentic.py`).
3. ✅ The SEARCH loop and `propose_transaction` (`agent/agentic.py`, `prompts/agent.search.j2`).
4. ✅ Confirm, recognise, policy, denial and escalation reusing `evaluate_dispute`, `open_dispute`, `block_card`, `create_handoff`.
5. ✅ The summary (`agent/summary.py`, `prompts/agent.summary.j2`), carried into the case queue as `facts.context` (verified) and `facts.customer_said.narrative` (unverified).
6. 🟡 The eval runner supports agentic mode (`python -m evals.runner --agent-mode agentic`, `make eval-smoke-agentic`): offline, 17 of 17 dev cases pass with a scripted agent and a reactive customer, the same as the workflow. That is a wiring result. The flexible-matching cases are written (`dispute-flex-*`, 8 cases, need a model). ⬜ The live A/B with the real model on them, the other new cases (two same-day charges with injection, a customer who rejects the proposal, a denial then escalation) and error analysis (`/error-analysis`) are still to do.
7. ⬜ ADR and the rest of `docs/requirements.md` if adopted.

## 11. What is not verified

- **No eval delta.** The runner now supports agentic mode, but only offline: the agent is a rule-based stand-in (`_scripted_agent_step`) and the customer a reactive script (`_ReactiveUser`). Both modes pass the same 17 dev cases, which shows the wiring, the sandbox, the policy and the safety checks work end to end, and says nothing about how a model searches. No live model was run. Rule 3 is not satisfied yet, so the mode stays off by default.
- **Tool calling was tested against a mocked provider only** (`httpx` mock transport), not against the live API. `make llm-smoke-tools` is the check to run first: it does one tool call and sends its result back the way `LLM.step` does, and prints a hint if the provider asks for the model's reasoning items to be replayed. Items are sent without provider ids or reasoning items (`store=False`); that is expected to be valid but has not been seen working.
- **The text checks may over-reject.** They are the workflow's own (`action_claims`, `ungrounded_number`) over what the agent may say: query results, the customer's words and the date parts of dates in results, plus a check that a capitalised word in mid-sentence (a merchant) is in a query result or the customer's words. A counting phrase, a derived figure or a proper name the agent adds costs a retry, then a code-written question. The first word of a sentence and short all-capital codes are not checked, so a merchant written at the start of a sentence is not verified. Real traffic will say how often good replies are refused.
- **A reply to the confirmation card that is neither yes nor no goes back to the agent** (a correction such as "no, it was another one for about 80"); an affirmative with extra words ("yes, that one") only gets the plain question again, because only a plain yes authorizes. Unit-tested with a scripted classifier; how the real classifier splits these is not seen.
- **Search prompt guidance is untested with a real model** (relaxing one detail at a time, listing the last five transactions when the customer remembers nothing).
- **A query cannot build a value over 20,000 characters** (`printf('%10000000d')` made 10 MB in 30 ms before; the time limit alone did not stop it).
- **Concurrency.** The query runs in `asyncio.to_thread`, so a slow query of one customer cannot freeze the other conversations (before, SQLite ran on the event loop and a bad query held it for up to the 0.5 s deadline; tests show three slow queries now overlap). What stays process-local: `ConversationStore`, so several API servers would need a shared store (`docs/requirements.md`, P2.1).
- **The sandbox holds a customer's whole history in memory per turn** (max 150 rows in this data). Fine here; a customer with thousands of rows would need paging, and `list_history` refuses more than 500 instead of cutting.
- **Console:** cases from agentic mode carry `facts.context`; the console page renders it as its own table (nested values appear as JSON, as `customer_said` already does). The frontend type check was run, the page was not looked at in a browser.

## Integration with main's flexible matching

The latest integration keeps both `TransactionStore.search_pool` (workflow matching) and `list_history`
(agentic SQL). Eval decoys accept both field conventions (`date`/`transaction_date`, `type`/`transaction_type`,
`category`/`merchant_category`). The scripted search now handles relative dates, transaction kind, category,
merchant prefixes on a relaxed search, and numbered picks. These are offline diagnostics, not production logic.

Main's workflow now also supports flexible matching. The earlier exact-search comparison is historical;
its failures must not be used as the current baseline. Integration tests now expect it to resolve the supported
flexible cases; the scripted extractor still cannot read the Portuguese merchant phrase in one fixture.
A matched live comparison after this integration remains pending.


## Follow-up after the updated live A/B

ADR 0017 accepts only the opt-in experiment; workflow remains the default. All non-consenting replies
within a pending confirmation episode share a bounded counter, including affirmative non-consent,
free-text detours and answers to subsequent search questions. An explicit card answer resets it.
The reactive customer now continues its scripted ambiguous replies in SEARCH instead of silently stopping.
The Starbucks fixture explicitly says it does not remember the expected amount if asked; it invents no amount.
Evidence, including the failed intermediate validation, is recorded in the follow-up report.
