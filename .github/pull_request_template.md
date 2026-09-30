## What and why

## How it was verified
- [ ] `make ci` passes locally
- [ ] Eval delta attached (required if prompts, models, tools, policy or the agent changed): run id, cases × trials, before → after per metric, with intervals

## Checklist
- [ ] No secrets, `.env` or restricted data in the diff
- [ ] Docs updated (`docs/known_issues.md` for data issues, an ADR for architectural decisions)
- [ ] The locked test split was not modified or used for tuning
