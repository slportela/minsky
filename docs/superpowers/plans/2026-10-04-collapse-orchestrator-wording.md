# Collapse orchestrator and wording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicated control flow and duplicated Spanish/Portuguese sentences in the dispute agent, with the same replies, the same state, and the same refusals as today.

**Architecture:** Four orchestrator edits share a path that is already written twice. Two wording edits move sentences and month names into tables the way `_REASONS` and `_NOUNS` already work. No prompt, tool, policy, or customer-visible sentence changes. Characterization tests are expected to pass before each edit; they are the lock, and they must still pass after it. The one new function (`month_index`) is the exception: its test fails until the function exists.

**Tech Stack:** Python 3.12, pytest, ruff (line length 120), pyright. No new dependencies.

**Spec:** This plan. Checked against AGENTS rules 1–3. Consent stays in code (`agent.consent`). A write is still reported only after tool read-back. Orchestration structure changes; customer-visible behavior does not, so no eval case is edited.

## Global Constraints

- Execute the tasks in order. Each task shows the full target so the end state is visible.
- Branch from `main`: `refactor/collapse-orchestrator-wording`. Do not commit to `main`.
- A missing `pending_question` returns `"unclear"` before `explicit_yes` or `explicit_no` run. `"sí"` with no stored question does not authorize. `state.confirmation` stays the model's decision, including when the return value is `"unclear"` or `"no"`.
- A numeric pick keeps the candidate list already stored. Do not send that pick through `_after_candidates([txn])`, which would replace the list with one id.
- `LLMNotConfiguredError` and `ModelMismatchError` still propagate from `_speak_safe` and from clarification. They subclass `RuntimeError`. A canned sentence covers a reply that failed the checks, and it does not cover a missing key or a model other than the pinned one.
- Copy the customer sentences exactly, including `¿`, accents, and `ç`. Do not rephrase.
- Leave `speak.py` claim patterns, negation and future windows, number grounding, consent token order, and `detect` versus `recognize` as they are.
- The test split is locked. Do not edit `evals/cases/`.
- Python ≥ 3.12, type hints on public functions, ruff line length 120.
- No silent fallbacks. An act with no code-written sentence still raises `ValueError`.

## File structure

| File | Responsibility |
|---|---|
| `backend/src/minsky_api/agent/orchestrator.py` | One consent function. One clarify branch for zero and many matches. One helper that asks the customer to confirm a selected transaction. Clarification goes through `_speak_safe`. |
| `backend/src/minsky_api/agent/wording.py` | Sentence tables. `month_index` built from the month tuples. Public sentence functions keep their signatures. |
| `backend/src/minsky_api/agent/speak.py` | Date parsing calls `month_index`. The second month table goes away. |
| `backend/tests/test_agent_orchestrator.py` | Locks: no question means no write; a pick keeps both candidate ids; a refused clarification and a missing key stay as they are. |
| `backend/tests/test_agent_wording.py` | Locks every code-written sentence, and the month index. |
| `backend/tests/test_agent_speak.py` | Existing ungrounded-number tests. They must stay green after the month table moves. |

---

### Task 1: One consent function

**Files:**
- Modify: `backend/src/minsky_api/agent/orchestrator.py` (`_remember_decision` at lines 417–428 and `_consent` at lines 431–443)
- Test: `backend/tests/test_agent_orchestrator.py`

**Interfaces:**
- Consumes: `classify_reply`, `ClassifyReplyArgs`, `explicit_yes`, `explicit_no`, `state.pending_question`.
- Produces: `_consent(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str`. Callers stay `_phase_confirm_txn`, `_phase_confirm_act`, `_phase_card_offer`. `_remember_decision` is deleted. Return values remain `"yes"`, `"no"`, and `"unclear"`.

The two functions both bail out when `pending_question` is missing. Deleting only the bail-out in `_consent` is a behavior change: `_remember_decision` would return `"unclear"`, and the next line `explicit_yes("sí")` would then return `"yes"`. Fold the classifier call into `_consent` and keep a single bail-out above the token checks.

- [ ] **Step 1: Run the tests that lock this**

Run:

```bash
uv run pytest backend/tests/test_agent_orchestrator.py::test_confirm_without_a_stored_question_does_not_act backend/tests/test_agent_orchestrator.py::test_an_explicit_yes_proceeds_whatever_the_model_recorded backend/tests/test_agent_orchestrator.py::test_blank_confirmation_stays_in_phase_and_opens_nothing -v
```

Expected: PASS. `test_confirm_without_a_stored_question_does_not_act` sends `"sí"` with `pending_question is None` and expects `confirmation == "unclear"`, no `classify_reply`, and no `open_dispute`.

- [ ] **Step 2: Replace both functions with one**

Delete `_remember_decision`. Replace `_consent` with:

```python
async def _consent(ctx: ToolContext, state: ConversationState, text: str, llm: LLM) -> str:
    """Classify for the audit trail. Only an explicit yes may authorize a write.

    The model's decision is stored on state.confirmation. The return value is what may
    authorize a write. A missing question is unclear before any token is read, so a bare
    "sí" with nothing stored does not authorize.
    """
    if not state.pending_question:
        state.confirmation = "unclear"
        return "unclear"
    result = await classify_reply(
        ctx,
        ClassifyReplyArgs(question=state.pending_question, text=text),
        llm,
    )
    state.confirmation = result.decision
    if explicit_no(text):
        return "no"
    if explicit_yes(text):
        return "yes"
    if result.decision == "no":
        return "no"
    return "unclear"
```

- [ ] **Step 3: Re-run the same tests**

Run the command from Step 1.

Expected: PASS. Also run:

```bash
uv run pytest backend/tests/test_agent_orchestrator.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/minsky_api/agent/orchestrator.py
git commit -m "$(cat <<'EOF'
Fold reply classification into the consent check.

A missing stored question must stay unclear before yes/no tokens are read.
EOF
)"
```

---

### Task 2: One branch when the search is not exactly one transaction

**Files:**
- Modify: `backend/src/minsky_api/agent/orchestrator.py` (`_after_candidates`, about lines 269–299)
- Test: `backend/tests/test_agent_orchestrator.py`

**Interfaces:**
- Consumes: `_clarify`, `_handoff`, `_candidate_list`, `_lang`, `get_settings().max_clarify_attempts`.
- Produces: the same `_after_candidates` signature. Zero matches and many matches share one branch. The single-match tail is unchanged in this task.

- [ ] **Step 1: Run the tests that lock both shapes**

Run:

```bash
uv run pytest backend/tests/test_agent_orchestrator.py::test_clarify_with_no_match_also_falls_back_to_a_code_written_question backend/tests/test_agent_orchestrator.py::test_clarify_sends_a_code_written_question_when_both_attempts_are_refused backend/tests/test_agent_orchestrator.py::test_clarify_exhausted_handoff backend/tests/test_agent_orchestrator.py::test_empty_extract_does_not_list_latest_txns backend/tests/test_agent_orchestrator.py::test_clarify_many_then_pick -v
```

Expected: PASS. The empty path leaves `candidate_txn_ids == []`. The many path leaves both ids. Both set `Phase.CLARIFY` unless the clarify limit hands off.

- [ ] **Step 2: Merge the two branches**

Replace the `len(txns) == 0` block and the `len(txns) > 1` block with one block. Leave the `txn = txns[0]` tail as it is.

```python
    if len(txns) != 1:
        state.clarify_count += 1
        if state.clarify_count > get_settings().max_clarify_attempts:
            return await _handoff(ctx, state, llm, reason="clarify_exhausted")
        candidates = _candidate_list(txns, _lang(state)) if txns else None
        reply = await _clarify(state, llm, candidates)
        state.phase = Phase.CLARIFY
        state.candidate_txn_ids = [t.transaction_id for t in txns]
        state.pending_question = None
        return reply
```

`_candidate_list` of an empty list is `""`. Pass `None` for that case so `_clarify` asks for merchant, amount, or date. An empty list still stores `candidate_txn_ids == []`.

- [ ] **Step 3: Re-run the Step 1 command**

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/minsky_api/agent/orchestrator.py
git commit -m "$(cat <<'EOF'
Share the clarify path for zero and many matches.

An empty search still asks for details and stores no candidate ids.
EOF
)"
```

---

### Task 3: One helper to confirm the selected transaction

**Files:**
- Modify: `backend/src/minsky_api/agent/orchestrator.py` (single-match tail of `_after_candidates`, and the numeric pick in `_phase_clarify`)
- Test: `backend/tests/test_agent_orchestrator.py` (`test_clarify_many_then_pick`)

**Interfaces:**
- Consumes: `_speak_safe`, `_txn_facts`, `_accept`, `_ask`, `with_yes_no_hint`, `_lang`, `Phase.CONFIRM_TXN`.
- Produces: `_confirm_selected(state: ConversationState, llm: LLM, txn: TransactionView) -> str`. It sets `selected_txn_id`, `selected_product_id`, and `selected_type`. It does not set `candidate_txn_ids`.

- [ ] **Step 1: Lock the pick invariant**

In `test_clarify_many_then_pick`, after the turn that sends `"2"`, add:

```python
    assert state.phase == Phase.CONFIRM_TXN
    assert state.selected_txn_id == "T2"
    assert state.candidate_txn_ids == ["T1", "T2"]
    assert "Cafe Sur" in reply
```

The existing test already asserts phase and `"Cafe Sur"`. Add the two id assertions next to those lines. Do not delete the earlier assert that the first turn listed `"1."` and `"2."`.

Run:

```bash
uv run pytest backend/tests/test_agent_orchestrator.py::test_clarify_many_then_pick -v
```

Expected: PASS on the current code. The list stays `["T1", "T2"]` because the pick path does not assign `candidate_txn_ids`.

- [ ] **Step 2: Add the helper and call it from both sites**

Add this above `_after_candidates`:

```python
async def _confirm_selected(state: ConversationState, llm: LLM, txn: TransactionView) -> str:
    """Ask the customer to confirm one transaction that code already selected.

    Does not touch candidate_txn_ids. A numeric pick must keep the list the customer chose from.
    """
    state.selected_txn_id = txn.transaction_id
    state.selected_product_id = txn.product_id
    state.selected_type = txn.transaction_type
    speech = await _speak_safe(state, llm, ("confirm_txn",), **_txn_facts(txn, _lang(state)))
    return _ask(state, Phase.CONFIRM_TXN, with_yes_no_hint(_accept(state, speech), _lang(state)))
```

In `_after_candidates`, the single-match tail becomes:

```python
    txn = txns[0]
    state.candidate_txn_ids = [txn.transaction_id]
    return await _confirm_selected(state, llm, txn)
```

In `_phase_clarify`, the successful pick becomes:

```python
            txn = await _get_owned_txn(ctx, state.candidate_txn_ids[index])
            if txn is None:
                return await _after_candidates(ctx, state, [], llm)
            return await _confirm_selected(state, llm, txn)
```

Leave the `txn is None` call on `_after_candidates`. That path is an empty search, not a confirmation.

- [ ] **Step 3: Re-run the pick test and the single-match confirm tests**

```bash
uv run pytest backend/tests/test_agent_orchestrator.py::test_clarify_many_then_pick backend/tests/test_agent_orchestrator.py::test_asking_for_confirmation_stores_the_question backend/tests/test_agent_orchestrator.py::test_a_refused_transaction_question_after_picking_a_candidate_is_a_code_written_one backend/tests/test_agent_orchestrator.py -q
```

Expected: PASS, including `candidate_txn_ids == ["T1", "T2"]` after picking `"2"`.

- [ ] **Step 4: Commit**

```bash
git add backend/src/minsky_api/agent/orchestrator.py backend/tests/test_agent_orchestrator.py
git commit -m "$(cat <<'EOF'
Share the question that confirms one selected transaction.

A numeric pick still keeps the candidate list the customer chose from.
EOF
)"
```

---

### Task 4: Clarification goes through the safe speaker

**Files:**
- Modify: `backend/src/minsky_api/agent/orchestrator.py` (`_clarify`, about lines 136–153, and the wording import)
- Test: `backend/tests/test_agent_orchestrator.py`

**Interfaces:**
- Consumes: `_speak_safe`, `_accept`, `with_candidates`, `safe_sentence` (inside `_speak_safe`). `safe_sentence("clarify", ...)` already calls `clarify_fallback`.
- Produces: `_clarify(state: ConversationState, llm: LLM, candidates: str | None = None) -> str`. Same return text as today.

`_clarify` repeats the try/except that `_speak_safe` already has. On a check failure, `_speak_safe` returns a `Speech` whose text is `safe_sentence`, which for `"clarify"` is `clarify_fallback`. `claims_card_blocked` on that speech stays `False`, so `_accept` appends `"clarify"` and does not set the card claim. When the fallback text already contains every option, `with_candidates` returns it unchanged.

- [ ] **Step 1: Run the clarification locks**

```bash
uv run pytest backend/tests/test_agent_orchestrator.py::test_clarify_accepts_a_model_that_punctuates_the_options_its_own_way backend/tests/test_agent_orchestrator.py::test_clarify_appends_the_exact_list_when_the_model_names_no_options backend/tests/test_agent_orchestrator.py::test_clarify_sends_a_code_written_question_when_both_attempts_are_refused backend/tests/test_agent_orchestrator.py::test_clarify_with_no_match_also_falls_back_to_a_code_written_question backend/tests/test_agent_orchestrator.py::test_a_missing_llm_key_is_not_hidden_by_the_clarify_fallback -v
```

Expected: PASS. The refused-twice reply equals `clarify_fallback` exactly (the list appears once). A missing key raises `LLMNotConfiguredError`.

- [ ] **Step 2: Replace `_clarify`**

```python
async def _clarify(state: ConversationState, llm: LLM, candidates: str | None = None) -> str:
    """Ask which transaction. The model writes the question; code owns the option list.

    The list is appended unless the model already carried every option exactly. If the model
    cannot phrase a valid question, _speak_safe supplies clarify_fallback. A missing key or a
    model other than the pinned one still fails loudly.
    """
    facts = {"candidates": candidates} if candidates else {}
    speech = await _speak_safe(state, llm, ("clarify",), **facts)
    reply = _accept(state, speech)
    return with_candidates(reply, candidates) if candidates else reply
```

Remove `clarify_fallback` from the orchestrator import. `wording.py` still exports it, and the tests import it from there.

- [ ] **Step 3: Re-run the Step 1 command, then the module**

Expected: PASS. If the fallback list appears twice, `with_candidates` was applied to a string that did not already contain each option line. `clarify_fallback` includes the raw `candidates` block, and `with_candidates` looks for the text after `". "`.

```bash
uv run pytest backend/tests/test_agent_orchestrator.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/minsky_api/agent/orchestrator.py
git commit -m "$(cat <<'EOF'
Build a refused clarification through the safe speaker.

A missing model key still surfaces, and the option list is still written once.
EOF
)"
```

---

### Task 5: Sentence tables

**Files:**
- Modify: `backend/src/minsky_api/agent/wording.py` (from `fallback_sentence` through `done_fallback`)
- Test: `backend/tests/test_agent_wording.py`

**Interfaces:**
- Consumes: `_lang`, `policy_reason`, `_this`.
- Produces: the same public functions and signatures: `fallback_sentence`, `confirm_question`, `with_yes_no_hint`, `clarify_fallback`, `safe_sentence`, `inform_fallback`, `done_fallback`. `with_candidates`, `human_date`, `human_amount`, `policy_reason`, and `transaction_noun` stay as they are.

- [ ] **Step 1: Lock the current sentences**

Add this test to `backend/tests/test_agent_wording.py`. Import `confirm_question`, `done_fallback`, and `inform_fallback` next to the existing wording imports.

```python
def test_code_written_sentences_stay_byte_for_byte():
    assert fallback_sentence("es", {}) == "Listo, registramos tu solicitud."
    assert fallback_sentence("pt", {}) == "Pronto, registramos a sua solicitação."
    assert fallback_sentence("es", {"handoff_id": "HO-1", "card_blocked": True}) == (
        "Tu tarjeta ya está bloqueada para proteger tu dinero. "
        "Un especialista de nuestro equipo tomará tu caso. Tu referencia es HO-1."
    )
    assert fallback_sentence("pt", {"dispute_id": "DSP-9"}) == (
        "Sua contestação DSP-9 está registrada; nossa equipe vai analisá-la e avisaremos cada avanço."
    )
    assert fallback_sentence("es", {"existing_dispute_id": "DSP-9"}) == (
        "Tu reclamo DSP-9 quedó registrado; nuestro equipo lo revisará y te avisaremos de cada avance."
    )

    assert confirm_question("confirm_open", "es", "Purchase") == (
        "¿Abro el reclamo por este cargo? Responde sí o no."
    )
    assert confirm_question("confirm_open", "pt", "Purchase") == (
        "Posso abrir a contestação de esta cobrança? Responda sim ou não."
    )
    assert confirm_question("offer_block", "es") == "¿Bloqueo tu tarjeta ahora? Responde sí o no."
    assert confirm_question("offer_block", "pt") == "Posso bloquear o seu cartão agora? Responda sim ou não."
    assert confirm_question("other", "es") == "¿Bloqueo tu tarjeta ahora? Responde sí o no."

    assert safe_sentence("ask_again", "es", {}) == "No me quedó claro. ¿Puedes responder sí o no?"
    assert safe_sentence("ask_again", "pt", {}) == "Não ficou claro. Pode responder sim ou não?"
    assert safe_sentence("abort", "es", {}) == (
        "Entendido, no haré nada con este caso. Si necesitas algo más, escríbeme aquí."
    )
    assert safe_sentence("abort", "pt", {}) == (
        "Entendido, não vou fazer nada com este caso. Se precisar de algo mais, escreva aqui."
    )
    assert safe_sentence("confirm_open", "es", {}) == "Con lo que me contaste, puedo seguir."
    assert safe_sentence("offer_block", "pt", {}) == "Com o que você me contou, posso seguir."
    assert safe_sentence("confirm_txn", "es", _TXN) == (
        "¿Reconoces el movimiento de Cafe por 25.00 USD del 10 de junio de 2026? Responde sí o no."
    )
    assert safe_sentence("confirm_txn", "es", {**_TXN, "when": None}) == (
        "¿Reconoces el movimiento de Cafe por 25.00 USD? Responde sí o no."
    )
    assert safe_sentence("confirm_txn", "pt", _TXN) == (
        "Você reconhece a transação de Cafe no valor de 25.00 USD em 10 de junio de 2026? Responda sim ou não."
    )
    assert safe_sentence("confirm_txn", "pt", {**_TXN, "when": None}) == (
        "Você reconhece a transação de Cafe no valor de 25.00 USD? Responda sim ou não."
    )

    assert clarify_fallback("es", None) == (
        "No encontré esa transacción. ¿Puedes decirme el comercio, el monto o la fecha?"
    )
    assert clarify_fallback("pt", None) == (
        "Não encontrei essa transação. Pode me dizer o estabelecimento, o valor ou a data?"
    )
    assert clarify_fallback("es", _LIST) == (
        "Encontré más de una transacción que coincide. ¿Cuál es la que quieres revisar?\n" + _LIST
    )
    assert clarify_fallback("pt", _LIST) == (
        "Encontrei mais de uma transação que corresponde. Qual delas você quer revisar?\n" + _LIST
    )

    assert done_fallback("es") == (
        "Tu caso ya quedó registrado con la referencia que te envié. Si necesitas algo más, escríbeme aquí."
    )
    assert done_fallback("pt") == (
        "O seu caso já está registrado com a referência que enviei. Se precisar de outra coisa, escreva aqui."
    )
    assert inform_fallback("es", "D04-already", "DSP-1") == (
        "Ya hay un reclamo abierto para ese cargo. La referencia de tu reclamo es DSP-1."
    )
    assert inform_fallback("pt", "D04-already", "DSP-1") == (
        "Já existe uma contestação aberta para essa cobrança. A referência da sua contestação é DSP-1."
    )
    assert inform_fallback("es", None, None) == done_fallback("es")
```

Run:

```bash
uv run pytest backend/tests/test_agent_wording.py::test_code_written_sentences_stay_byte_for_byte -v
```

Expected: PASS before any wording edit. If a string does not match, the test captured a typo. Fix the test to the sentence the function returns today. Do not change the function to satisfy a mistyped expectation.

- [ ] **Step 2: Replace the language forks with tables**

Keep `_lang`, `_REASONS`, `_NOUNS`, `human_date`, `human_amount`, `policy_reason`, `transaction_noun`, `_this`, and `with_candidates`. Replace `fallback_sentence`, `confirm_question`, `with_yes_no_hint`, `clarify_fallback`, `safe_sentence`, `inform_fallback`, and `done_fallback` with the following. The assembly order of `fallback_sentence` stays card, then dispute id, then handoff id, then the generic sentence.

```python
_SENTENCES: dict[str, dict[str, str]] = {
    "es": {
        "card_blocked": "Tu tarjeta ya está bloqueada para proteger tu dinero.",
        "dispute": (
            "Tu reclamo {dispute_id} quedó registrado; nuestro equipo lo revisará y te avisaremos de cada avance."
        ),
        "handoff": "Un especialista de nuestro equipo tomará tu caso. Tu referencia es {handoff_id}.",
        "recorded": "Listo, registramos tu solicitud.",
        "yes_no": "Responde sí o no.",
        "clarify_many": "Encontré más de una transacción que coincide. ¿Cuál es la que quieres revisar?",
        "clarify_none": "No encontré esa transacción. ¿Puedes decirme el comercio, el monto o la fecha?",
        "ask_again": "No me quedó claro. ¿Puedes responder sí o no?",
        "abort": "Entendido, no haré nada con este caso. Si necesitas algo más, escríbeme aquí.",
        "can_continue": "Con lo que me contaste, puedo seguir.",
        "confirm_txn": "¿Reconoces el movimiento de {merchant} por {amount}{when}? Responde sí o no.",
        "confirm_open": "¿Abro el reclamo por {this}? Responde sí o no.",
        "offer_block": "¿Bloqueo tu tarjeta ahora? Responde sí o no.",
        "dispute_ref": " La referencia de tu reclamo es {existing_dispute_id}.",
        "done": (
            "Tu caso ya quedó registrado con la referencia que te envié. Si necesitas algo más, escríbeme aquí."
        ),
    },
    "pt": {
        "card_blocked": "Seu cartão já está bloqueado para proteger o seu dinheiro.",
        "dispute": (
            "Sua contestação {dispute_id} está registrada; nossa equipe vai analisá-la e avisaremos cada avanço."
        ),
        "handoff": "Um especialista da nossa equipe vai assumir o seu caso. Sua referência é {handoff_id}.",
        "recorded": "Pronto, registramos a sua solicitação.",
        "yes_no": "Responda sim ou não.",
        "clarify_many": "Encontrei mais de uma transação que corresponde. Qual delas você quer revisar?",
        "clarify_none": "Não encontrei essa transação. Pode me dizer o estabelecimento, o valor ou a data?",
        "ask_again": "Não ficou claro. Pode responder sim ou não?",
        "abort": "Entendido, não vou fazer nada com este caso. Se precisar de algo mais, escreva aqui.",
        "can_continue": "Com o que você me contou, posso seguir.",
        "confirm_txn": (
            "Você reconhece a transação de {merchant} no valor de {amount}{when}? Responda sim ou não."
        ),
        "confirm_open": "Posso abrir a contestação de {this}? Responda sim ou não.",
        "offer_block": "Posso bloquear o seu cartão agora? Responda sim ou não.",
        "dispute_ref": " A referência da sua contestação é {existing_dispute_id}.",
        "done": "O seu caso já está registrado com a referência que enviei. Se precisar de outra coisa, escreva aqui.",
    },
}


def _say(language: str, key: str, **slots: object) -> str:
    return _SENTENCES[_lang(language)][key].format(**slots)


def fallback_sentence(language: str, facts: dict[str, object]) -> str:
    """A complete reply about a write that already happened, for when the model cannot phrase it."""
    dispute_id = facts.get("dispute_id") or facts.get("existing_dispute_id")
    handoff_id = facts.get("handoff_id")
    parts: list[str] = []
    if facts.get("card_blocked") is True:
        parts.append(_say(language, "card_blocked"))
    if isinstance(dispute_id, str) and dispute_id:
        parts.append(_say(language, "dispute", dispute_id=dispute_id))
    if isinstance(handoff_id, str) and handoff_id:
        parts.append(_say(language, "handoff", handoff_id=handoff_id))
    if not parts:
        parts.append(_say(language, "recorded"))
    return " ".join(parts)


def confirm_question(act: str, language: str, transaction_type: str | None = None) -> str:
    key = "confirm_open" if act == "confirm_open" else "offer_block"
    return _say(language, key, this=_this(transaction_type, language))


def with_yes_no_hint(text: str, language: str) -> str:
    """The transaction question is the model's. Code makes sure it says how to answer.

    Only a plain "sí" or "no" authorizes anything (agent.consent), and the questions for the two actions already
    end with "Responde sí o no" from code. A question that already asks for a yes or a no is left alone.
    """
    folded = text.casefold()
    if "sí o no" in folded or "sim ou não" in folded or "si o no" in folded:
        return text
    return f"{text.rstrip()} {_say(language, 'yes_no')}"


def clarify_fallback(language: str, candidates: str | None) -> str:
    """A complete clarification when the model cannot phrase one: ask which transaction, or ask for details."""
    if candidates:
        return f"{_say(language, 'clarify_many')}\n{candidates}"
    return _say(language, "clarify_none")


def safe_sentence(act: str, language: str, facts: dict[str, object]) -> str:
    """A code-written sentence for an act when the model cannot phrase a valid one.

    It says only what the code already knows from `facts` (the reason a policy rule gives, the transaction the
    customer must confirm). It never reports an action. An act with no sentence here is a bug and raises.
    """
    candidates = facts.get("candidates")
    if act == "clarify":
        return clarify_fallback(language, candidates if isinstance(candidates, str) else None)
    if act == "ask_again":
        return _say(language, "ask_again")
    if act == "abort":
        return _say(language, "abort")
    if act in ("confirm_open", "offer_block"):
        reason = facts.get("reason")
        if isinstance(reason, str) and reason:
            return reason[:1].upper() + reason[1:] + "."
        return _say(language, "can_continue")
    if act == "confirm_txn":
        when = facts.get("when")
        when_text = ""
        if when:
            when_text = f" em {when}" if _lang(language) == "pt" else f" del {when}"
        return _say(
            language,
            "confirm_txn",
            merchant=facts.get("merchant"),
            amount=facts.get("amount"),
            when=when_text,
        )
    raise ValueError(f"no code-written sentence for act {act!r}")


def inform_fallback(language: str, rule_id: str | None, existing_dispute_id: str | None) -> str:
    """A complete, safe answer when the model cannot phrase a policy explanation (for example D04)."""
    reason = policy_reason(rule_id, language) or ""
    text = reason[:1].upper() + reason[1:] + "." if reason else ""
    if existing_dispute_id:
        text += _say(language, "dispute_ref", existing_dispute_id=existing_dispute_id)
    return text.strip() or done_fallback(language)


def done_fallback(language: str) -> str:
    """After the conversation's case is settled: no new facts, only what to do next."""
    return _say(language, "done")
```

`confirm_question("confirm_open", ...)` passes `this=` into `.format`. The `offer_block` template has no `{this}` placeholder, and `str.format` ignores extra keyword arguments. That keeps one call for both acts.

The `{when}` slot is `""` or `" del …"` / `" em …"`, including the leading space, so the sentence with no date has a single space before `?`.

- [ ] **Step 3: Run the wording tests**

```bash
uv run pytest backend/tests/test_agent_wording.py backend/tests/test_agent_orchestrator.py -q
```

Expected: PASS. Format `KeyError` means a template placeholder name does not match the keyword. A byte mismatch means a space or accent drifted. Restore the sentence from Step 1.

- [ ] **Step 4: Commit**

```bash
git add backend/src/minsky_api/agent/wording.py backend/tests/test_agent_wording.py
git commit -m "$(cat <<'EOF'
Store customer-facing sentences in language tables.

The sentences stay the ones the tests already lock.
EOF
)"
```

---

### Task 6: One month table

**Files:**
- Modify: `backend/src/minsky_api/agent/wording.py` (`_MONTHS`, `human_date`)
- Modify: `backend/src/minsky_api/agent/speak.py` (`_MONTHS` at about lines 188–199, and `_fact_dates`)
- Test: `backend/tests/test_agent_wording.py`
- Test: `backend/tests/test_agent_speak.py` (existing `ungrounded_number` cases; do not change them)

**Interfaces:**
- Consumes: `wording._MONTHS`, the tuples `human_date` already uses.
- Produces: `month_index(name: str) -> int | None`. `1` is enero/janeiro. Unknown names return `None`. `human_date` stays on the tuples.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_agent_wording.py`:

```python
def test_month_index_matches_the_names_human_date_speaks():
    from minsky_api.agent.wording import month_index

    assert month_index("junio") == 6
    assert month_index("Junio") == 6
    assert month_index("março") == 3
    assert month_index("Março") == 3
    assert month_index("abril") == 4
    assert month_index("agosto") == 8
    assert month_index("cargo") is None
    assert human_date(date(2026, 6, 10), "es") == "10 de junio de 2026"
    assert human_date(date(2026, 3, 1), "pt") == "1 de março de 2026"
```

Run:

```bash
uv run pytest backend/tests/test_agent_wording.py::test_month_index_matches_the_names_human_date_speaks -v
```

Expected: FAIL with `ImportError` (`month_index` is not defined).

- [ ] **Step 2: Add `month_index` next to `_MONTHS`**

```python
def month_index(name: str) -> int | None:
    """1-based month for a Spanish or Portuguese name. Unknown names return None."""
    folded = name.casefold()
    for names in _MONTHS.values():
        for index, month in enumerate(names, start=1):
            if month.casefold() == folded:
                return index
    return None
```

`abril` and `agosto` appear in both languages at the same index. Returning the first hit matches the dict `speak.py` builds today.

Run the Step 1 test again. Expected: PASS.

- [ ] **Step 3: Point date parsing at `month_index`**

In `speak.py`, add `month_index` to a wording import:

```python
from minsky_api.agent.wording import month_index
```

Delete the `_MONTHS` dict in `speak.py` (the `name: index` comprehension). In `_fact_dates`, replace the month lookup:

```python
    for day, month, year in _HUMAN_DATE.findall(text):
        index = month_index(month)
        if index is not None:
            dates.add(f"{year}-{index:02d}-{int(day):02d}")
```

`wording.py` does not import `speak.py`. This import does not cycle.

- [ ] **Step 4: Run date grounding and wording tests**

```bash
uv run pytest backend/tests/test_agent_wording.py backend/tests/test_agent_speak.py -q
```

Expected: PASS. `ungrounded_number("Tu cargo del 2026-06-10.", {"when": "10 de junio de 2026", ...})` stays `None`, because junio still maps to 6.

- [ ] **Step 5: Commit**

```bash
git add backend/src/minsky_api/agent/wording.py backend/src/minsky_api/agent/speak.py backend/tests/test_agent_wording.py
git commit -m "$(cat <<'EOF'
Share month names between spoken dates and date checks.

Parsing still accepts the Spanish and Portuguese names the replies use.
EOF
)"
```

---

### Task 7: Format, types, and the orchestrator suite

**Files:**
- Modify: none, unless ruff or pyright reports a problem in the files this plan touched.

**Interfaces:**
- Consumes: the tree after Tasks 1–6.
- Produces: a green check of the touched modules.

- [ ] **Step 1: Lint and format the touched files**

```bash
uv run ruff check backend/src/minsky_api/agent/orchestrator.py backend/src/minsky_api/agent/wording.py backend/src/minsky_api/agent/speak.py backend/tests/test_agent_wording.py backend/tests/test_agent_orchestrator.py
uv run ruff format backend/src/minsky_api/agent/orchestrator.py backend/src/minsky_api/agent/wording.py backend/src/minsky_api/agent/speak.py backend/tests/test_agent_wording.py backend/tests/test_agent_orchestrator.py
```

Expected: ruff check reports no issues. If format changes a file, keep that change.

- [ ] **Step 2: Typecheck and re-run the touched tests**

```bash
uv run pyright backend/src/minsky_api/agent/orchestrator.py backend/src/minsky_api/agent/wording.py backend/src/minsky_api/agent/speak.py
uv run pytest backend/tests/test_agent_orchestrator.py backend/tests/test_agent_wording.py backend/tests/test_agent_speak.py -q
```

Expected: pyright reports no errors in those files. Pytest PASS.

- [ ] **Step 3: Full CI before a PR**

```bash
make ci
```

Expected: PASS. Do not edit eval cases to get there. If CI fails outside the files this plan touched, stop and report that failure. Do not expand the refactor to fix it.

- [ ] **Step 4: Commit formatting only if Step 1 changed files**

```bash
git add backend/src/minsky_api/agent/orchestrator.py backend/src/minsky_api/agent/wording.py backend/src/minsky_api/agent/speak.py backend/tests/test_agent_wording.py backend/tests/test_agent_orchestrator.py
git commit -m "$(cat <<'EOF'
Format the collapsed orchestrator and wording.

EOF
)"
```

Skip the commit when `git status` shows no changes.
