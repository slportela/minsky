# 0003. Agent runtime: Messages API with our own orchestrator

- Status: proposed
- Date: 2026-09-26

## Context
Permissions and policy must be enforced outside model-generated text. The workflow has known steps, and Anthropic's guidance is to start with the simplest pattern (workflows over open-ended agents when the steps are known). The Claude Agent SDK is the Claude Code harness as a library: it runs the bundled CLI as a subprocess, and its built-in tools (file, shell) stay in the toolset unless explicitly disallowed ("`allowed_tools` ... does not remove tools from Claude's toolset"). Managed Agents runs the loop and a sandbox on Anthropic's infrastructure, which ADR 0001 rules out (models only through Bedrock).

## Decision
- Customer-facing runtime: models on **Amazon Bedrock** (for Claude, the Anthropic SDK's Bedrock client, which keeps strict tools, structured outputs and prompt caching), called from **our own orchestrator**, a state machine that owns every decision. The LLM understands and phrases; code decides and acts.
- A thin model interface, so other Bedrock models can be compared in the evals.
- The Claude Agent SDK / Claude Code is used for **development and offline jobs** (case generation, error analysis, audits), not in the customer path.

## Consequences
- More code we own, and all of it testable (L0) and visible to reviewers.
- Bedrock has no server-side fallbacks or batches (ADR 0001): retries and fallback are ours, tested in the tool-failure eval cases.
