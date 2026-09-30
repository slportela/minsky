@AGENTS.md

## Claude Code

- Shared settings live in `.claude/settings.json`: an allowlist for the usual commands, and deny rules for `.env`, the generated silver models and the locked test split. Personal overrides go in `.claude/settings.local.json` (git-ignored).
- Skills: `/new-eval-case` turns a failure or requirement into an eval case; `/error-analysis` builds a failure taxonomy from an eval run.
- When you finish a change, run `make ci` and report the result as it is, failures included.
