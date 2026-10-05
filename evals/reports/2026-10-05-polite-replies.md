# Polite replies: offline evidence (incomplete — no live run)

Change: `prompts/agent.speak.j2` v5 → v6 and the code-owned customer sentences in
`backend/src/minsky_api/agent/wording.py`. Goal: replies that read like a bank agent rather
than a form, with no loss of correctness or safety.

**This is not an eval delta.** The paid dev run against the pinned model was not executed
(the credential was not available in the run environment), so nothing here measures the
model-written text. What follows is what the offline harness and the unit suite can prove.
A live `--extractor real --trials 3` run on the dev split is still required before merge.

## What was verified

| Check | Result |
|---|---|
| `make ci` | green: 520 unit tests, eval-check clean, 4/4 regression smoke, 17/17 offline dev smoke, frontend typecheck and API tests |
| Claim reading of every code-owned sentence, before vs. after | no new claim in any of the 34 sentences across `es` and `pt`; one pre-existing claim removed |
| Live dev run (48 trials, pass rate, provider cost) | **not run** |

The offline dev smoke runs with no provider, and its stub emits bag-of-facts text rather than
the fallbacks, so the 17/17 above exercises `with_yes_no_hint` but not the rewritten
fallback sentences. Those rest on unit tests, one per path, as they did before.

## Claim reading, before and after

The risk this change carries is that warmer phrasing trips the grader's claim reader
(`evals/claims.py`), which is deliberately broader than the product's own check: its handoff
stems include `revis\w*` near `especialista`, and its refund stems include `recuper\w*` near
`dinero`. Every code-owned sentence was read by both checkers in both languages, on the
committed version and the previous one. The flags are the same in both:

| Sentence | Claims | Reference present |
|---|---|---|
| `safe_sentence` for clarify, ask_again, abort, confirm_open, offer_block, confirm_txn | none | n/a |
| `confirm_question` for confirm_open, offer_block | none | n/a |
| `clarify_fallback`, `inform_fallback` (D01) | none | n/a |
| `fallback_sentence` after an open | `dispute_opened` | `DSP-…` in the text |
| `fallback_sentence` after a block and handoff | `card_blocked`, `handoff` | `HO-…` in the text, `card_blocked` in facts |
| `inform_fallback` (D04) | `dispute_opened` | `DSP-…` in the text |
| `done_fallback` | none (was `dispute_opened`) | names a reference only when one was reported |

Three tests pin the invariant: in `tests/test_eval_claims.py`, sentences before a write claim
nothing and sentences after a write carry the reference for what they claim; in
`backend/tests/test_agent_orchestrator.py`, a follow-up after an abort names no reference.

### One pre-existing claim removed

`done_fallback` used to say "Tu caso ya quedó registrado con la referencia que te envié" on
every settled conversation, which the grader reads as `dispute_opened`. But `Phase.DONE` is
reached with nothing written on two routes — the terminal policy inform (D01-D03) and an abort —
so on those the sentence claimed an action that never happened and would have raised
`UNVERIFIED_ACTION_CLAIM` had the model failed on that turn. No eval case had hit it.

`ConversationState` now carries the id already reported to the customer (`state.reference`),
recorded in `_public_facts` where every id passes through, and `done_fallback` names a
reference only when there is one. All six variants (two languages, no reference / dispute id /
handoff id) now claim nothing.

## Before and after (es, unless noted)

| Turn | Before | After |
|---|---|---|
| Transaction question (model refused) | ¿Reconoces el movimiento de Cafe por 25.00 USD del 10 de junio de 2026? Responde sí o no. | Gracias por contarme. Encontré esta transacción: Cafe, 25.00 USD, 10 de junio de 2026. ¿Es esa la que quieres revisar? Responde sí o no, por favor. |
| Open confirmation (D09) | El cargo cumple las condiciones para abrir el reclamo ahora mismo. ¿Abro el reclamo por este cargo? Responde sí o no. | Gracias por confirmarlo. El cargo cumple las condiciones para abrir el reclamo ahora mismo. ¿Quieres que abra el reclamo por este cargo? Responde sí o no, por favor. |
| Card-block offer (D06) | Si no hiciste esta compra, alguien podría estar usando tu tarjeta. ¿Bloqueo tu tarjeta ahora? Responde sí o no. | Gracias por confirmarlo. Si no hiciste esta compra, alguien podría estar usando tu tarjeta. ¿Quieres que bloquee tu tarjeta ahora, para proteger tu cuenta? Responde sí o no, por favor. |
| Unclear answer | No me quedó claro. ¿Puedes responder sí o no? | Disculpa, no te entendí bien. Responde sí o no, por favor. |
| Customer declines | Entendido, no haré nada con este caso. Si necesitas algo más, escríbeme aquí. | Entendido, gracias por decírmelo: no haré nada con este caso. Si necesitas algo más, escríbeme aquí y te ayudo. |
| No transaction found | No encontré esa transacción. ¿Puedes decirme el comercio, el monto o la fecha? | Disculpa, no encontré esa transacción. ¿Me puedes decir el comercio, el monto o la fecha, por favor? Con cualquiera de esos datos la busco de nuevo. |
| Several candidates | Encontré más de una transacción que coincide. ¿Cuál es la que quieres revisar? | Gracias por los datos. Encontré más de una transacción que coincide y prefiero no elegir por ti: ¿cuál es la que quieres revisar? |
| After the open | Tu reclamo DSP-… quedó registrado; nuestro equipo lo revisará y te avisaremos de cada avance. | Gracias por escribirnos. Tu reclamo DSP-… quedó registrado; nuestro equipo lo revisará y te avisaremos de cada avance. Quedo aquí si necesitas algo más. |
| D04, already disputed | Ya hay un reclamo abierto para ese cargo. La referencia de tu reclamo es DSP-…. | Gracias por contárnoslo. Ya hay un reclamo abierto para ese cargo. La referencia de tu reclamo es DSP-…. Si tienes otra duda sobre este caso, escríbeme aquí. |
| Settled case, a case was opened | Tu caso ya quedó registrado con la referencia que te envié. Si necesitas algo más, escríbeme aquí. | Gracias por escribirme de nuevo. La referencia de tu caso es DSP-…, y nuestro equipo te avisará de cada avance. Si necesitas algo más, cuéntame y te ayudo. |
| Settled case, nothing was written (D01-D03, abort) | Tu caso ya quedó registrado con la referencia que te envié. Si necesitas algo más, escríbeme aquí. *(false)* | Gracias por escribirme de nuevo. Sobre este caso ya te compartí lo que tengo. Si necesitas algo más, cuéntame y te ayudo. |
| Open confirmation (pt) | Posso abrir a contestação **de esta** cobrança? Responda sim ou não. | Você quer que eu **conteste** esta cobrança? Responda sim ou não, por favor. |

The last row is a grammar fix. `a contestação de esta cobrança` needs the preposition contracted
(`desta`), and the old sentence read wrong in every `confirm_open` turn in `pt`. Rather than
contract it, the question now uses the verb `contestar` — already the verb in the D03 and D05
reasons — so there is no preposition to get wrong and no helper to maintain.

The `confirm_txn` row is more than a tone change. The old fallback asked a *recognition*
question ("¿Reconoces el movimiento…?") while `_phase_confirm_txn` reads the answer as an
*identification* one. A customer disputing an unrecognized charge would naturally answer "no" —
that is the whole content of their complaint — and the orchestrator would discard the correct
transaction and return to clarify. The prompt always had the right semantics; only the code
fallback was inverted. No eval case could catch it: every scripted confirmation reply is `sí`.

### The acknowledgement had to be weaker than first written

The lead-in on `fallback_sentence` started as "Gracias por confirmarlo", on the assumption that
the path only runs after a confirmation. It does not. Four of its five call sites have no
confirmation behind them — `out_of_scope` (which fires on the customer's *first* message),
`clarify_exhausted`, `max_turns`, and `possible_fraud_no_block`, which runs precisely because
the customer answered **no** to the card block. It is now "Gracias por escribirnos", which
holds on every path, with two orchestrator tests driving the two paths that broke it.

## What politeness could not change

- The literal `Responde sí o no` / `Responda sim ou não` stays in every confirmation. Only an
  exact token authorizes a write (`agent/consent.py`), so softening to "¿te parece bien?"
  would invite replies that authorize nothing and send the customer around the loop again.
  It is now written once (`wording.yes_no_hint`) and detected once (`wording.asks_yes_or_no`),
  so the sentence we append and the sentence we look for cannot drift. The cost is that
  `ask_again` lost its question form: "Disculpa, no te entendí bien. Responde sí o no, por favor"
  is a shade less gentle than the question it replaced, on the turn where the customer is already
  confused. It repeats the exact instruction they did not follow, which is the trade accepted.
- The two action offers stay questions. A declarative "bloqueo tu tarjeta" is read as a
  completed block by `evals/claims.py`; the question form and an offer marker keep it an offer.
- No reassurance about the money returning, and no mention of a specialist unless `facts`
  carries a `handoff_id`. Both are stated as prohibitions in the prompt.

## Limitations

- No live run: the model-written text, which is most of what a customer reads, is unmeasured
  by this report. The headline claim after a live run can only be "correctness unchanged, tone
  judged by reading" — there is no tone grader (the LLM judge is not built, `evals/README.md`).
- `max_output_tokens` for the speech call went from 400 to 500 to fit four sentences. Each call
  reserves its output limit against the budget, so a live dev run's conservative estimate rises
  (USD 0.13 for 48 trials at the recorded rates); actual spend is usage-priced and far lower.
