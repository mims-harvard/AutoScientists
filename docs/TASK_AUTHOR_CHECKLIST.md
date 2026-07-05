# Task Author Checklist

Use this checklist before adding or launching a new `task-<name>/` directory.

## Required Files

- `TASK.md` exists at the task leaf directory passed to `--task`.
- `TASK.md` starts with YAML frontmatter containing `name` and `task_type`.
- `task_type` is one of `optimization`, `biomlbench`, or `proteingym`.
- A matching `LAUNCH.md` exists in the task directory or a parent task-family
  directory inside this repository.

## Data and Baselines

- Large downloaded data is excluded from git unless it is intentionally bundled.
- Reference answers, held-out labels, and private submissions live under
  `private/` or `autoscientists_submission/` so `launch.py` excludes them from
  agent-visible runs.
- Baseline scripts document expected runtime, hardware, metric direction, and
  the command needed to reproduce the baseline.
- Any required environment variables are listed in the task README.

## Validate Before Running

Run this before starting ClawInstitute or Claude Code:

```bash
python launch.py check-task --task task-protein-gym --validate-only
```

For BioML-Bench, pass a leaf task rather than the family root:

```bash
python launch.py check-lipo \
  --task task-biomlbench/drug_discovery/tdcommons-lipophilicity-astrazeneca \
  --validate-only
```

The command verifies `TASK.md`, resolves the task type, finds the effective
`LAUNCH.md`, and exits without requiring ClawInstitute credentials.
