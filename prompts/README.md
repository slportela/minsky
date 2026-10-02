# Prompts

Every prompt the system or the eval harness sends to a model lives here, as a versioned file. Rules (see AGENTS.md):

- One file per prompt, named `<component>.<purpose>.md` or `<component>.<purpose>.j2` (Jinja2), with a header comment: `version`, `owner`, `model`, `changed` (date + why).
- Agent replies and the extract system prompt are Jinja2 (`agent.*.j2`), rendered by `minsky_api.agent.prompts.render`.
- Bump `version` on every change and attach the eval delta to the PR.
- Never paste eval cases here as examples: `make eval-check` fails if case text appears under `prompts/`.
- No timestamps or per-request data in the static part (it breaks prompt caching). Per-turn facts go in as Jinja variables only.
