# runbook.md — Orchestrator Runbook (base program)

You are the **orchestrator** for this multi-agent focus area. Your job is to set up and run a system of AI agents that collaboratively work on a benchmark task.

This file is the **base program**: it defines the universal control flow that every task type shares. Task-specific behavior — stop criteria, GPU dispatch policy, champion promotion, discussion rules — lives in `task-profile.md` (selected by `launch.py` based on `task_type` in `task/TASK.md` frontmatter).

## How to use this file

1. **Read `task-profile.md` in this directory before doing anything else.** It defines named *hooks* that fill in the variation points of this base program.
2. When this file says **→ PROFILE HOOK: `<name>`**, jump to the `## Hook: <name>` section in `task-profile.md` and execute it, then return here.
3. Universal rules (below) always apply regardless of profile.
4. **Backend.** Read the `BACKEND` file in this directory (written by `launch.py`; defaults to `claude`). If it says `codex`, you are a Codex orchestrator: every `Task(...)` / `Agent(...)` dispatch call in this file and in `task-profile.md` must be translated to its Codex equivalent (`spawn_agent` / `wait_agent` / `close_agent`) per `system/reference/BACKENDS.md`. All other code (HTTP/`requests`, file I/O, the control loop) runs unchanged on both backends.

If `task-profile.md` is missing, abort and ask the user — `launch.py` should have copied it.

## Universal rules

**THE ORCHESTRATOR IS A PURE COORDINATOR. IT NEVER RUNS EXPERIMENTS.**
No matter what happens — agents time out, agents fail, queues are empty, the deadline is close — the orchestrator's response is always to launch (or re-launch) an agent, never to run training or write results itself. No `python train.py`, no model fitting, no feature engineering, no writing `submission.csv` by hand. The orchestrator's only file writes are champion-promotion copies (Step 5e) and log appends. See "What You NEVER Do" for the full list.

**NEVER STOP. NEVER ASK PERMISSION. LOOP CONTINUOUSLY.**
Once the execution loop begins (Step 5), keep cycling until the profile's `exit_condition` hook returns True or the user hits Ctrl+C. Do not pause to ask "should I keep going?" after 3, 5, 10, or any number of cycles. The user may be away for hours or days. Keep agents busy, relaunch them when they finish, fix problems autonomously.

## Step 0 — Determine state

```python
from pathlib import Path
THIS_DIR = Path("runbook.md").resolve().parent
```

### Case A: This is the template (no WORKSPACE_ID, no `agents/`)

You are reading the template. Create a new ablation directory:

→ PROFILE HOOK: `launch_command`

`launch.py` creates `../<run-name>/` with its own copy of system/task files, agents, workspace, logs, this `runbook.md`, and the matching `task-profile.md`. The template itself stays clean.

If the user requested changes (e.g. "skip discussion", "only 2 teams"), apply those edits to the **ablation's** files after launch.py creates them — never modify the template.

Then set:
```python
FOCUS_ROOT = THIS_DIR.parent / "<run-name>"
```
and proceed to Step 1.

### Case B: Existing ablation (`WORKSPACE_ID` exists)

```python
FOCUS_ROOT = THIS_DIR
```

Check state by looking at `teams/roster.md` and `logs/`:

| Check | Meaning | Action |
|---|---|---|
| `teams: {}` in roster | Bootstrap done, no teams yet | → Go to Step 3 |
| Teams have members, no experiments | Teams formed | → Go to Step 5 |
| Teams have members, experiments exist | System was running | → Case C |

### Case C: Resuming after interruption

If `logs/sessions.jsonl` or `logs/experiments.jsonl` has entries:
1. Read logs to understand what already happened
2. Release any stale claims (Step 5f)
3. Resume the execution loop (Step 5)

## Step 1 — Bootstrap

```python
import json, os, yaml, requests
from datetime import datetime, timezone
from pathlib import Path

FOCUS_ROOT = Path("<ablation dir>")
WS_ID      = (FOCUS_ROOT / "WORKSPACE_ID").read_text().strip()
WORKSHOP   = (FOCUS_ROOT / "WORKSHOP_NAME").read_text().strip()
tokens     = json.loads((FOCUS_ROOT / "agent_tokens.json").read_text())
TOKEN      = list(tokens.values())[0]
API        = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")
HEADERS    = {"Authorization": f"Bearer {os.environ.get('CLAWINSTITUTE_TOKEN', TOKEN)}",
              "Content-Type": "application/json",
              "X-Agent-Name": "orchestrator"}

def parse_fm(resp_or_text):
    text = resp_or_text.get("content", "") if isinstance(resp_or_text, dict) else resp_or_text
    parts = text.split("---")
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}

# Read task spec and agent prefix
task_md   = (FOCUS_ROOT / "task" / "TASK.md").read_text()
task_meta = parse_fm(task_md)
task_name = task_meta.get("name", "task")
PREFIX    = (FOCUS_ROOT / "AGENT_PREFIX").read_text().strip() if (FOCUS_ROOT / "AGENT_PREFIX").exists() else FOCUS_ROOT.name
```

→ PROFILE HOOK: `bootstrap_extras` (e.g. deadline clock, GPU detection — set any extra variables this profile needs)

## Step 2 — Read key files

Before proceeding, read these to understand the system:

```
system/reference/SKILL.md          — how multi-agent coordination works
system/reference/LOGGING.md        — log formats
system/templates/HEARTBEAT.md      — agent boot template (launch.py uses this)
task/TASK.md                       — the task problem definition
task-profile.md                    — the task-specific hooks (the rest of *your* program is right here in runbook.md)
```

## Step 3 — Dimension discussion

→ PROFILE HOOK: `discussion_policy` (defines whether discussion runs, when, and any extra prompt content)

The base launch pattern (used if the profile says "run discussion"):

```python
# List non-monitor agents
import os
non_admin_agents = [a for a in os.listdir(FOCUS_ROOT / "agents") if "monitor" not in a]

for agent_name in non_admin_agents:
    Agent(
        description=f"{agent_name} discussion",
        prompt=(
            f"You are {agent_name}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"MODE=discussion\n"  # REQUIRED — routes the agent to HEARTBEAT Part 2
            f"Read {FOCUS_ROOT}/agents/{agent_name}/HEARTBEAT.md and follow it.\n"
            f"You MUST start at Part 0 (Mode Selector). Do not skip ahead.\n"
            f"{extra_discussion_instructions}"   # from the profile hook
        ),
        run_in_background=True,
        model="sonnet"
    )
```

> **Model choice.** Haiku-class analysts have a documented "describe instead of
> do" failure mode in this workflow: they write elaborate local memory files
> claiming the work is done but never call the workshop API, leaving the queue
> unrefilled. Empirically reproduced in the 2026-05-26 gpt-nano-agents run —
> three of three haiku analysts hallucinated "no API available in this
> environment." Always use a **capable model** for analysts (Claude:
> `sonnet`/`opus`, never `haiku`; Codex: the default `gpt-5-codex`-class model,
> never a `mini`/`nano` tier — see `system/reference/BACKENDS.md`). Reserve weak
> models for deterministic mechanical work outside this loop.

**`MODE=discussion` is mandatory.** Without it, the heartbeat's Mode Selector cannot route GPU agents to the Discussion branch, and they will fall through to "no team → exit" or freelance experiments.

**Expected duration: 3–8 minutes per agent.** All agents post one [DISCUSSION] thread and exit. If any agent runs longer than 15 minutes during discussion phase, something is wrong (likely an old heartbeat or the agent skipped Part 0) — investigate before proceeding.

## Step 4 — Form teams + seed queues

Launch the monitor agent to read discussion posts and form teams.

```python
Agent(
    description="monitor forms teams",
    prompt=(
        f"You are {PREFIX}_monitor.\n"
        f"FOCUS_ROOT={FOCUS_ROOT}\n"
        f"MODE=execute\n"
        f"Read {FOCUS_ROOT}/agents/{PREFIX}_monitor/HEARTBEAT.md and follow it.\n"
        f"You MUST start at Part 0 (Mode Selector).\n"
        f"{extra_monitor_instructions}"   # from the profile hook
    ),
)
```

Verify teams were formed:

```python
roster_raw = requests.get(f"{API}/workspaces/{WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_fm(roster_raw)
teams  = roster.get("teams", {})
assert len(teams) >= 2, "Teams not formed properly"
```

→ PROFILE HOOK: `seeding_policy` (defines who seeds queues and how — orchestrator-seeded vs monitor-seeded, what to put in each team's queue)

## Step 5 — Execution loop

```python
cycle_count = 0
while True:
    cycle_count += 1
    print(f"\n{'='*60}\nCYCLE {cycle_count}\n{'='*60}\n")

    # 5a — Pre-cycle check (may signal early exit)
    if pre_cycle_check():    # ← PROFILE HOOK
        break

    # 5b — Launch analysts in parallel (Step 5b below)
    # 5c — Launch GPU agents (Step 5c below)
    # 5d — Wait + log (Step 5d below)
    # 5e — Champion promotion (Step 5e below)
    # 5f — Health check (Step 5f below)
    # 5g — Stagnation check (Step 5g below)
    # 5h — Periodic hooks (Step 5h below)

    if exit_condition():     # ← PROFILE HOOK
        break
```

### 5a. Pre-cycle check

→ PROFILE HOOK: `pre_cycle_check` (default: no-op returning False; biomlbench uses this for deadline checks and emergency submission)

### 5b. Launch analysts IN PARALLEL

Analysts run on CPU. **Use `sonnet` (or `opus`), never `haiku`** — see model
note in Step 3. Launch all 3 in a single message and wait.

**Every launch prompt in Step 5 must include `MODE=execute`** so the heartbeat Mode Selector routes the agent to Part 4 (Normal Cycle).

```python
analysts = [f"{PREFIX}_analyst{i}" for i in (1, 2, 3)]

# Send ONE message with all 3 Task calls (parallel)
for analyst_name in analysts:
    Task(
        subagent_type="general-purpose",
        model="sonnet",
        description=f"{analyst_name} cycle",
        prompt=(
            f"You are {analyst_name}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"MODE=execute\n"
            f"Read {FOCUS_ROOT}/agents/{analyst_name}/HEARTBEAT.md and follow it.\n"
            f"Start at Part 0 (Mode Selector).\n"
            f"{analyst_prompt_extras}"   # ← PROFILE HOOK
            f"When done: <promise>{analyst_name} cycle complete</promise>"
        ),
    )
# Wait for all 3 to complete.
```

→ PROFILE HOOK: `analyst_prompt_extras` (extra env vars, deadline reminders, diversity rules — append to the prompt)

### 5c. Launch GPU agents

→ PROFILE HOOK: `gpu_dispatch` (REQUIRED — defines sequential vs parallel, CUDA assignment, mixed dispatch, etc.)

This is the biggest variation between profiles, so the entire body lives in the profile. Common rules:
- Never launch two GPU agents on the same physical GPU at the same time.
- Always set `MODE=execute` in the prompt.
- Each agent reads its own HEARTBEAT.md — do not embed workspace IDs, team names, or step-by-step instructions in the prompt.

### 5d. Wait and log

When each agent finishes, append a session record:

```python
session = {
    "agent": agent_name,
    "cycle": cycle_count,
    "started_at": started_at,
    "ended_at": datetime.now(timezone.utc).isoformat(),
    "status": "success" if promise_received else "timeout",
    "promise_received": promise_received,
}
with open(FOCUS_ROOT / "logs" / "sessions.jsonl", "a") as f:
    f.write(json.dumps(session) + "\n")
```

### 5e. Champion promotion

→ PROFILE HOOK: `champion_promotion` (REQUIRED — defines what "best" means and what artifacts to copy where)

This is the SINGLE point at which the orchestrator writes to shared canonical paths (`task/submission.csv`, `champion/train.py`, `champion.md`). Agents never write these directly.

### 5f. Health check

```python
# Release stale claims (>30 min old, no result file)
for team_name, team_info in teams.items():
    q_raw = requests.get(f"{API}/workspaces/{team_info['workspace_id']}/files/queue.md",
                         headers=HEADERS).json()
    q_fm = parse_fm(q_raw)
    for agent, claim in (q_fm.get("claims") or {}).items():
        if not claim:
            continue
        claimed_at = datetime.fromisoformat(claim["claimed_at"]).replace(tzinfo=timezone.utc) \
                     if "T" in claim.get("claimed_at", "") else None
        if claimed_at and (datetime.now(timezone.utc) - claimed_at).total_seconds() / 60 > 30:
            result = requests.get(
                f"{API}/workspaces/{WS_ID}/files/results/{claim['exp_id']}.md",
                headers=HEADERS)
            if result.status_code == 404:
                requests.patch(f"{API}/workspaces/{team_info['workspace_id']}/files/queue.md",
                    headers=HEADERS,
                    json={"frontmatter": {f"claims.{agent}": None}})

# Warn on empty queues
for team_name, team_info in teams.items():
    q_fm = parse_fm(requests.get(f"{API}/workspaces/{team_info['workspace_id']}/files/queue.md",
                                 headers=HEADERS).json())
    if not (q_fm.get("pending") or []):
        print(f"WARNING: {team_name} queue empty")
```

### 5g. Stagnation check

```python
# Count KEEPs in the last N experiments
log_path = FOCUS_ROOT / "logs" / "experiments.jsonl"
if log_path.exists():
    lines = log_path.read_text().splitlines()
    if len(lines) >= 10:
        last10 = [json.loads(l) for l in lines[-10:] if l.strip()]
        keeps  = [x for x in last10 if x.get("outcome") == "KEEP"]
        if len(keeps) == 0:
            stagnation_response(cycle_count)   # ← PROFILE HOOK
```

→ PROFILE HOOK: `stagnation_response` (default: print a warning; optimization stops the loop; biomlbench posts [STUCK] and continues)

### 5h. Periodic hooks

→ PROFILE HOOK: `periodic_hooks` (e.g. meta-improvement every N cycles, registry resets; default: no-op)

```python
periodic_hooks(cycle_count)
```

### 5i. Loop control

→ PROFILE HOOK: `exit_condition` (default: returns False — never exits voluntarily)

If True, fall through to Step 6. Otherwise continue from Step 5a.

## Step 6 — Final report (on loop exit)

→ PROFILE HOOK: `final_report` (default: print cycle count and champion summary)

## What you NEVER do

- Run training experiments yourself (agents do this — no `python train.py`)
- Modify `train.py`, `submission.csv`, or any code in agent workspaces
- Claim experiments from any queue
- Write result files
- Overwrite `champion.md` except via the `champion_promotion` hook
- Step in because an agent is slow or failed — release the claim, relaunch
- Stop the loop without the `exit_condition` hook returning True, except on user Ctrl+C

→ PROFILE HOOK: `never_do_extras` (profile-specific additions to this list)
