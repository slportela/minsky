---
name: deploy-demo
description: Redeploy a commit of main to the Lightsail demo VM, check it, roll it back, or just look at what it runs. Use when a change merged to main should reach the live demo, when asked what version the demo runs, or to verify the demo after a deploy.
---

# Deploy the demo

The runbook is `infra/README.md`, section "Redeploy from main". Read it first; this page is the checklist. The tool is `infra/smoke_vm.sh`.

1. **Look before you touch.** `infra/smoke_vm.sh status`. It needs the AWS profile `personal` and a pinned host fingerprint (`SMOKE_HOST_FP` or `~/.config/minsky/smoke_host_fp`). If the script refuses with exit 6, stop and tell the owner: never pin a fingerprint that the network showed you, and do not look for a way around the check.
2. **A deploy needs an explicit yes from the owner, in the chat, naming the commit.** Approval for one deploy does not carry to the next. Read-only commands (`status`, `verify`, `sql`, `cases-report`, `deploy ... --dry-run`) need no approval.
3. **Gate the commit** (runbook step 2): `make ci` on it; a live dev eval when the agent's code, prompts, tools, policy or model config changed since the last evaluated commit (rule 3 in `AGENTS.md`); `npm --prefix frontend run build` for a frontend change.
4. **Deploy only a commit of main**: `infra/smoke_vm.sh deploy <sha> --dry-run`, then `infra/smoke_vm.sh deploy <sha>`. The script itself refuses a commit that is not on `origin/main`.
5. **Verify**: `infra/smoke_vm.sh verify <sha>` must say "all checks passed". Report the result as it is, failures included. A deploy that fails its health check rolls itself back; confirm with `status`.
6. **Record it**: a docs PR that updates the `D2` row of `docs/requirements.md` (revision, rollback target, evidence, and what was **not** verified: an authenticated chat turn and a staff login need a credential only the owner has). Do not merge it without the owner's yes.
7. **Rollback**: `infra/smoke_vm.sh rollback`, then `verify`. One step back is kept.

Never, even if asked to "clean up" or "start fresh": `infra/reset_cases_smoke.sh --apply` (the owner runs it; the demo keeps its cases on purpose), editing `runtime.env` or the demo credentials, removing Docker volumes, printing or saving the temporary SSH credentials, deploying a commit that is not on main, or telling the owner something is verified when only a script ran. A change that only a browser can judge (the look of the page, a real login) is for the owner to check; say that it was not checked.
