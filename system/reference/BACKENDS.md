---
name: multi-agent-focus-backends
description: Maps every Claude Code dispatch mechanism to its Codex equivalent so the system runs on either backend
---

# Backends — Claude Code ↔ Codex

AutoScientists runs on **either Claude Code or Codex**. The orchestrator is an LLM
session that interprets `runbook.md` and the per-task `task-profile.md`; the
agents are LLM sessions that interpret their `HEARTBEAT.md`. Both backends read
the same files and coordinate through the same ClawInstitute HTTP server.

`runbook.md` and the profiles show **Claude Code dispatch as the canonical
example**. This file maps every Claude-specific mechanism to its Codex
equivalent. **Which backend a run targets is recorded in the run directory's
`BACKEND` file** (written by `launch.py --backend {claude,codex}`, default
`claude`). The orchestrator reads `BACKEND` in Step 0 of `runbook.md` and, if it
says `codex`, translates every dispatch call as described below. All other code —
HTTP/`requests` calls, file I/O, the control loop — runs **unchanged** on both
backends.

## Dispatch mapping

| Concern | Claude Code | Codex |
|---|---|---|
| Orchestrator entrypoint | `claude -p "Read runbook.md and execute"` | `codex exec --enable multi_agent "Read runbook.md and execute"` |
| Spawn a subagent | `Task(...)` / `Agent(...)` | `spawn_agent` |
| N parallel subagents | N `Task`/`Agent` calls in one message | N `spawn_agent` calls |
| Await a subagent result | Task/Agent return value | `wait_agent` |
| Free the slot afterward | (automatic) | `close_agent` |
| Background dispatch | `run_in_background=True` | a `spawn_agent` call (inherently concurrent) |
| Skip permissions | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` |
| Ephemeral session | `--ephemeral` | `--ephemeral` |
| Project config auto-load | `CLAUDE.md` | `AGENTS.md` (neither is required — the orchestrator and agents are pointed at explicit files) |

**Translating a dispatch site.** Where a profile or the runbook shows, e.g.:

```python
Task(
    subagent_type="general-purpose",
    model="sonnet",
    prompt="You are ar2_gpu1.\nFOCUS_ROOT=...\nMODE=execute\nRead .../HEARTBEAT.md and follow it.",
)
# Wait for the agent, then read its <promise>.
```

a Codex orchestrator does the equivalent:

1. `spawn_agent` with the **same prompt** (the prompt text is backend-agnostic —
   it carries identity, `FOCUS_ROOT`, `MODE`, `CUDA_VISIBLE_DEVICES`, etc.).
2. `wait_agent` to get the result.
3. `close_agent` to free the slot.

For "send N `Task` calls in one message" (parallel analysts/GPU agents), issue N
`spawn_agent` calls, then `wait_agent` on each.

## The `<promise>` convention is backend-agnostic

Agents end a cycle by emitting a `<promise>...</promise>` line in their final
output. This is **plain text**, not a tool feature. The orchestrator reads it the
same way from a Task/Agent return value (Claude) or a `wait_agent` result
(Codex). No translation needed.

## Model policy

The loop depends on agents that actually *do* the work (call the workshop API),
not merely *describe* it. Weak models exhibit a "describe instead of do" failure
mode (see the failure-mode note in `runbook.md` Step 3).

- **Claude Code:** use `sonnet` or `opus`. **Never `haiku`** for analysts/GPU
  agents.
- **Codex:** inherit the configured default capable model (a `gpt-5-codex`-class
  model). **Never** a `mini`/`nano` tier.

Do **not** hardcode model IDs anywhere — they churn. Reserve weak models for
deterministic mechanical work outside this loop.

## Enabling Codex multi-agent dispatch

`spawn_agent` / `wait_agent` / `close_agent` require the Codex `multi_agent`
feature. Enable it **per invocation** on the orchestrator's entrypoint:

```bash
codex exec --enable multi_agent "Read /abs/path/<run-name>/runbook.md and execute"
```

`--enable multi_agent` is equivalent to `-c features.multi_agent=true` and needs
**no edit to `~/.codex/config.toml`**. (Optionally, a user may instead set
`[features] multi_agent = true` in `~/.codex/config.toml` to make it the
default.) Only the orchestrator needs this feature; leaf agents do not spawn
anyone.

**Concurrency cap.** Codex caps the number of *live* agents
(`max_concurrent_subagents`; 6 in the builds tested). Spawning past it fails
with `collab spawn failed: agent thread limit reached`. `runbook.md` Step 2
dispatches all 9 non-monitor agents in one batch, so on Codex split batch
dispatch into waves of <= 6, and `close_agent` each finished agent — completed
agents keep counting against the cap until closed.

## Sandbox, network, and working directory (read carefully)

Agents must (a) read/write files in the run directory, (b) reach the **localhost**
ClawInstitute server, and (c) for some tasks reach the network for `pip`/`git`,
and run training. Codex's default sandbox blocks network and restricts writes to
the workspace subtree — and the run directory is created as a **sibling** of the
template (`../<run-name>/`). So one of the following is required:

**Parity default** (matches Claude's `--dangerously-skip-permissions`):

```bash
codex exec --enable multi_agent --dangerously-bypass-approvals-and-sandbox \
  "Read /abs/path/<run-name>/runbook.md and execute"
```

**Hardened alternative** — `cd` into the run directory so it becomes the
workspace, and explicitly allow network:

```bash
cd /abs/path/<run-name>
codex exec --enable multi_agent -s workspace-write \
  -c sandbox_workspace_write.network_access=true --skip-git-repo-check \
  "Read runbook.md and execute"
```

`--skip-git-repo-check` is required: run directories are plain siblings of the
template, not git repos, and Codex otherwise aborts with *"Not inside a trusted
directory and --skip-git-repo-check was not specified."*
`--dangerously-bypass-approvals-and-sandbox` skips this check on its own.

`launch.py --backend codex` prints the parity-default command for you.
