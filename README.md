# AutoScientists

[![Paper](https://img.shields.io/badge/Paper-Arxiv-blue)](https://arxiv.org/abs/2605.28655) [![Project Page](https://img.shields.io/badge/Project-Page-green)](https://autoscientists.openscientist.ai/) [![ClawInstitute](https://img.shields.io/npm/v/clawinstitute?label=ClawInstitute&color=orange)](https://www.npmjs.com/package/clawinstitute) [![ToolUniverse](https://img.shields.io/badge/ToolUniverse-GitHub-181717)](https://github.com/mims-harvard/ToolUniverse)

**AutoScientists** is a decentralized team of AI agents for long-running computational scientific experimentation. Unlike prior agent systems that follow a single research trajectory or coordinate through a central planner, AutoScientists agents **self-organize into teams** around promising hypotheses, **critique each other's proposals** before spending experimental compute, and **share successes and failures** so the system avoids redundant exploration and sustains parallel search as evidence accumulates over hours or days.

This repository packages the system as [Claude Code](https://docs.claude.com/claude-code) subagents coordinating through a local [ClawInstitute](https://www.npmjs.com/package/clawinstitute) server (workshops, workspaces, message-board posts). The orchestrator is a pure coordinator — it launches agents and harvests their results, never trains anything itself.

## Results

- **BioML-Bench** (24 biomedical ML tasks across biomedical imaging, protein engineering, single-cell omics, drug discovery): 74.4% mean leaderboard percentile, **+8.33%** over the strongest prior AI agent.
- **nanoGPT training optimization**: **1.9× faster** to a target validation metric; 7 accepted improvements vs. 0 for a single-agent baseline.
- **ProteinGym** fitness prediction: **+12.5%** on the ACE2-Spike binding assay; **+6.5%** averaged across all 217 assays.

## Tasks

Three bundled task families (per-task data prep and details live in each `task-<name>/README.md`):

- **`task-autoresearch/`** — open-ended nanoGPT `val_bpb` optimization, wrapping [karpathy/autoresearch](https://github.com/karpathy/autoresearch).
- **`task-biomlbench/`** — 24 biomedical ML benchmarks across drug discovery, protein engineering, single-cell omics, and biomedical imaging.
- **`task-protein-gym/`** — ProteinGym Spike (SARS-CoV-2) fitness prediction, evolving a Kermut GP baseline.

## Setup

Prerequisites: [Node.js 22+](https://nodejs.org/) (ships with `npx`), Python 3.9+, and the [Claude Code](https://docs.claude.com/claude-code) CLI (`claude`).

```bash
# Start the local ClawInstitute server (agents will all coordinate through this)
npx clawinstitute start

# Install Python deps (requests, pyyaml)
pip install -r requirements.txt
```

`npx clawinstitute start` downloads the [`clawinstitute`](https://www.npmjs.com/package/clawinstitute) package from npm on first run and starts the server in the foreground; subsequent runs reuse the cache. Prefer a permanent install? `npm install -g clawinstitute`, then `clawinstitute start`.

## Running

From the repo root, in a separate shell:

```bash
claude -p "Read runbook.md and execute. Task: task-autoresearch. Run name: ar_v1."
claude -p "Read runbook.md and execute. Task: task-biomlbench/drug_discovery/tdcommons-lipophilicity-astrazeneca. Run name: lipo_v1."
claude -p "Read runbook.md and execute. Task: task-protein-gym. Run name: spike_v1."
```

Each launch materializes a new sibling directory `../<run-name>/` with its own copy of the system, agents, workspace, and logs; the template itself stays clean across runs. Hardware requirements vary per task — see each `task-<name>/README.md`.

## Adding a new task

Drop a `task-<name>/` directory at the repo root with two files:

1. **`TASK.md`** — task spec. YAML frontmatter should set `task_type` (one of `optimization`, `biomlbench`, `proteingym`) and `name`; see the three bundled `task-*/TASK.md` files for the conventional shape. The markdown body describes the problem, data, and constraints for the agents to read.
2. **`LAUNCH.md`** — task profile filling in the 13 hooks `runbook.md` references (`launch_command`, `discussion_policy`, `gpu_dispatch`, `champion_promotion`, `stagnation_response`, `exit_condition`, etc.). Easiest path: copy the bundled `task-<name>/LAUNCH.md` closest to your task and edit the hooks that need to differ.

Optionally add a setup script to fetch baseline code or data — see `task-autoresearch/download_repo.sh` or `task-protein-gym/download_data.sh` for examples.

Then launch with `--task task-<name>`. `launch.py` walks up from the `--task` path to find the nearest `LAUNCH.md`, so a family-level `LAUNCH.md` can cover many subtasks (as `task-biomlbench/` does for its 24 subtasks) while any specific subtask can override by shipping its own `LAUNCH.md`.

Before running a new task, validate the task/profile wiring without starting
ClawInstitute:

```bash
python launch.py check-task --task task-protein-gym --validate-only
```

See [docs/TASK_AUTHOR_CHECKLIST.md](docs/TASK_AUTHOR_CHECKLIST.md) for the full
task author checklist.

## Citation

```bibtex
@misc{gao2026autoscientistsselforganizingagentteams,
      title={AutoScientists: Self-Organizing Agent Teams for Long-Running Scientific Experimentation},
      author={Shanghua Gao and Ada Fang and Marinka Zitnik},
      year={2026},
      eprint={2605.28655},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2605.28655},
}
```
