#!/usr/bin/env python3
"""Launch a fresh AutoScientists experiment from this template.

Creates a NEW experiment directory, copies system/task files into it, then
bootstraps agents, workspace, and kickoff post.

Usage:
  python3 launch.py                                                        # auto-names: {template}_{timestamp}/
  python3 launch.py my-experiment                                          # creates ../my-experiment/
  python3 launch.py my-experiment --task task-protein-gym                 # bundled task (relative path)
  python3 launch.py my-experiment --task task-biomlbench/drug_discovery/tdcommons-lipophilicity-astrazeneca
  python3 launch.py my-experiment --task /absolute/path/to/task-dir
  python3 launch.py my-experiment --output-dir /tmp/runs                  # create in a specific parent dir

Task directories are bundled as subdirectories of this repo:
  task-protein-gym/           — ProteinGym Spike fitness prediction (evolves repo/kermut.py)
  task-biomlbench/<category>/<task>/  — BioMLBench supervised ML tasks (agents write from scratch)

Additional tasks can be pointed to via an absolute or relative path to any directory
containing a TASK.md file.

Task types (set via task_type: in TASK.md frontmatter):
  proteingym  — Kermut GP evolution task. Example:
                  python3 launch.py my-run --task task-protein-gym
  biomlbench  — Supervised ML benchmark; excludes train.py/submission.csv from task copy
                (agents build these from scratch). Example:
                  python3 launch.py my-run --task task-biomlbench/drug_discovery/tdcommons-lipophilicity-astrazeneca
  optimization — Open-ended optimization of a baseline (e.g. autoresearch nanoGPT
                val_bpb minimisation). Example:
                  python3 launch.py my-run --task task-autoresearch

Profile resolution: launch.py walks up from the --task path looking for the
nearest LAUNCH.md (bounded to this repo), letting a family-level LAUNCH.md
cover many subtasks while still allowing per-task overrides. Every task must
ship a LAUNCH.md somewhere on that walk; there is no generic fallback.

For all task types, after launch the orchestrator reads runbook.md + task-profile.md:
  cd <run-dir>
  # Open runbook.md in a Claude Code session and execute it step by step.
"""

import argparse
import json
import os
import shutil
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Template directory (where this script lives) ────────────

TEMPLATE_DIR = Path(__file__).resolve().parent

# ── ClawInstitute API ────────────────────────────────────────

API = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")

# Load admin (privileged bootstrap) token.
# Priority: template .key file > CLAWINSTITUTE_TOKEN env var > ~/.clawinstitute/token
def _load_token():
    for key_path in [TEMPLATE_DIR / ".key"]:
        if key_path.exists():
            return key_path.read_text().strip()
    if os.environ.get("CLAWINSTITUTE_TOKEN"):
        return os.environ["CLAWINSTITUTE_TOKEN"]
    default = Path.home() / ".clawinstitute" / "token"
    if default.exists():
        return default.read_text().strip()
    print("ERROR: No API key found. Start the local server first:")
    print("  npx clawinstitute start")
    print("Or set CLAWINSTITUTE_TOKEN explicitly.")
    sys.exit(1)

# ── Parse arguments ─────────────────────────────────────────

_TIMESTAMP = datetime.now(timezone.utc).strftime("%m%d_%H%M")

_parser = argparse.ArgumentParser(description="Launch a fresh AutoScientists experiment.")
_parser.add_argument("name", nargs="?", default=None,
                     help="Experiment name (default: {template}_{timestamp})")
_parser.add_argument("--task", default=None,
                     help="Path to a bundled task directory (e.g. task-protein-gym or task-biomlbench/drug_discovery/tdcommons-lipophilicity-astrazeneca) or an absolute path to any directory containing TASK.md. Defaults to $TASK_DIR.")
_parser.add_argument("--output-dir", default=None, metavar="DIR",
                     help="Parent directory for the new experiment (default: next to this template)")
_parser.add_argument("--protein", default=None, metavar="PROTEIN_ID",
                     help="Protein/assay to focus on, e.g. SPIKE_SARS2_Starr_2020_binding. "
                          "Substituted into task/TASK.md and task/LAUNCH.md after copying.")
_parser.add_argument("--mareforma-export", nargs="?", const=".", default=None, metavar="RUN_DIR",
                     help="Post-run mode: walk RUN_DIR (default: cwd) for "
                          "autoscientists_submission/research_insights.md files and sign each "
                          "one as a content-addressable mareforma claim into "
                          "RUN_DIR/.mareforma/graph.db. Skips the normal bootstrap. "
                          "Requires 'pip install mareforma[clawinstitute]>=0.3.3'.")
_args = _parser.parse_args()

# ── Post-run export mode: skip bootstrap, sign findings, exit ─────
if _args.mareforma_export is not None:
    try:
        import mareforma
    except ImportError:
        print("ERROR: --mareforma-export requires the mareforma package.")
        print("Install: pip install 'mareforma[clawinstitute]>=0.3.3'")
        sys.exit(1)
    import hashlib as _hashlib
    _run_dir = Path(_args.mareforma_export).resolve()
    _insight_files = sorted(_run_dir.glob("**/autoscientists_submission/research_insights.md"))
    if not _insight_files:
        print(f"No autoscientists_submission/research_insights.md found under {_run_dir}")
        sys.exit(0)
    _graph_dir = _run_dir / ".mareforma"
    _graph_dir.mkdir(exist_ok=True)
    with mareforma.open(_graph_dir) as _graph:
        _before = len(_graph.query("", limit=100000))
        for _path in _insight_files:
            _text = _path.read_text(encoding="utf-8", errors="replace")
            _task = _path.parent.parent.name
            # Content-aware idempotency: re-running on unchanged files is a no-op;
            # editing a file produces a new claim and preserves the old one.
            _idem = f"autoscientists/{_path.relative_to(_run_dir)}/{_hashlib.sha256(_text.encode()).hexdigest()[:16]}"
            _graph.assert_claim(
                text=_text,
                classification="ANALYTICAL",
                generated_by=f"agent/autoscientists/{_task}",
                source_name=_task,
                idempotency_key=_idem,
            )
            print(f"  {_path.relative_to(_run_dir)}")
        _after = len(_graph.query("", limit=100000))
    _new = _after - _before
    print(f"\n{_new} new claims signed, {_after - _new} already present. Graph: {_graph_dir}/graph.db")
    sys.exit(0)

# Load token after argparse so `--help` works without credentials.
ADMIN_TOKEN = _load_token()
HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}

RUN_NAME = _args.name or f"{TEMPLATE_DIR.name}_{_TIMESTAMP}"
PARENT_DIR = Path(_args.output_dir).resolve() if _args.output_dir else TEMPLATE_DIR.parent

# ── Create ablation directory ───────────────────────────────

RUN_DIR = PARENT_DIR / RUN_NAME

if RUN_DIR.exists():
    print(f"ERROR: {RUN_DIR} already exists. Pick a different name.")
    sys.exit(1)

# Copy task directory: --task flag > TASK_DIR env var.
task_source = _args.task or os.getenv("TASK_DIR")
if not task_source:
    print("ERROR: --task is required. Pass a bundled task directory or an absolute path:")
    print("  python3 launch.py my-run --task task-protein-gym")
    print("  python3 launch.py my-run --task task-biomlbench/drug_discovery/tdcommons-lipophilicity-astrazeneca")
    sys.exit(1)

# Resolve relative to template if not absolute, otherwise honour the absolute path.
_task_path = Path(task_source)
if not _task_path.is_absolute():
    _task_path = (TEMPLATE_DIR / task_source).resolve()
if not _task_path.is_dir():
    raise RuntimeError(f"Task directory not found: {_task_path}")
task_source = str(_task_path)

# Require TASK.md at the --task root. Without this, passing a task-family
# directory (e.g. --task task-biomlbench instead of a leaf subtask) would
# silently fall back to the family README, fail to parse its frontmatter,
# default to task_type=optimization, and copy the whole family tree into
# the run dir. Validate here, BEFORE mkdir, so failure leaves no empty dir.
if not (_task_path / "TASK.md").exists():
    print(f"ERROR: {task_source} has no TASK.md.")
    print(f"  --task must point at a leaf task directory containing TASK.md, not a family root.")
    print(f"  Examples:")
    print(f"    python3 launch.py my-run --task task-autoresearch")
    print(f"    python3 launch.py my-run --task task-biomlbench/drug_discovery/tdcommons-lipophilicity-astrazeneca")
    print(f"    python3 launch.py my-run --task task-protein-gym")
    sys.exit(1)

print(f"Creating experiment: {RUN_DIR}")
RUN_DIR.mkdir(parents=True)

# ── Detect task type from TASK.md frontmatter (read source before copy) ─────
import yaml as _yaml


def _read_task_md(task_dir):
    """Find a task description file in `task_dir`, falling back through a
    candidate list. Returns (text, source_name). Source_name is one of
    "TASK.md", "program.md", "README.md", or "synthesized" when none of
    the candidates exist (we synthesize a minimal stub so downstream code
    has frontmatter to parse instead of crashing)."""
    task_dir = Path(task_dir)
    for name in ("TASK.md", "program.md", "README.md"):
        p = task_dir / name
        if p.exists():
            return p.read_text(), name
    return (
        "---\ntask_type: optimization\nmetric: val_bpb\n---\n"
        f"# Task\n\nFound no TASK.md/program.md/README.md in {task_dir}. "
        "Using minimal defaults.\n",
        "synthesized",
    )


_task_type = "optimization"  # default
_task_md_content, _src_task_md_source = _read_task_md(Path(task_source))
_task_md_parts = _task_md_content.split("---")
if len(_task_md_parts) >= 3:
    _task_meta = _yaml.safe_load(_task_md_parts[1]) or {}
    _task_type = _task_meta.get("task_type", "optimization")

IS_BENCHMARK = _task_type == "biomlbench"
IS_PROTEINGYM = _task_type == "proteingym"

if IS_BENCHMARK:
    print(f"  Task type: biomlbench (train.py/submission.csv/answers.csv excluded from task copy; data included)")
    _wall_clock = 16 if "A100" in _task_md_content else 8
    _cpu_only = 'CUDA_VISIBLE_DEVICES=""' in _task_md_content or "CPU-only" in _task_md_content
    print(f"  Wall-clock limit: {_wall_clock}h  |  CPU-only: {_cpu_only}")
elif IS_PROTEINGYM:
    print(f"  Task type: proteingym (repo/kermut.py evolution; LAUNCH.md used as task-profile)")

# Copy template files into the ablation directory
shutil.copytree(TEMPLATE_DIR / "system", RUN_DIR / "system")

# Always copy to "task/" in run directory for consistency.
# For biomlbench tasks: exclude train.py and submission.csv — agents build these.
# data is always included so agents can train.
# autoscientists_submission/ and private/ are always excluded — they contain reference
# solutions and held-out answers that must never be visible to agents.
# .git is excluded so cloned upstream repos (e.g. task-autoresearch/repo from
# git clone karpathy/autoresearch) don't drag a hundreds-of-MB .git tree into
# every run directory; __pycache__ keeps stale bytecode from leaking across runs.
_always_exclude = ("autoscientists_submission", "private", ".git", "__pycache__")
if IS_BENCHMARK:
    _task_copy_ignore = shutil.ignore_patterns(*_always_exclude, "train.py", "submission.csv", "answers.csv", "test_original_kaggle_unlabelled", "ISSUE.md", "training_scripts")
else:
    _task_copy_ignore = shutil.ignore_patterns(*_always_exclude)
shutil.copytree(TEMPLATE_DIR / task_source, RUN_DIR / "task", ignore=_task_copy_ignore, symlinks=True)

# Copy the base runbook plus the matching task profile.
# The base file (`runbook.md`) defines the universal control flow and
# references named hooks; the profile (a LAUNCH.md bundled with the task,
# found by walking up from the --task path) fills in those hooks. The
# profile is renamed to `task-profile.md` in the run dir so the base file
# can always reference it by a fixed name.
program_file = "runbook.md"
base_src = TEMPLATE_DIR / program_file
if not base_src.exists():
    print(f"  WARNING: {program_file} not found in template — orchestrator program missing")
else:
    shutil.copy2(base_src, RUN_DIR / program_file)
    print(f"  Copied: {program_file}")

# Resolve the task-profile by walking up from the task dir looking for
# LAUNCH.md. Lets a family-level LAUNCH.md (e.g. task-biomlbench/LAUNCH.md)
# cover every subtask, while a per-task LAUNCH.md (e.g. inside
# task-protein-gym/ or a specific biomlbench subtask) takes precedence.
# Bounded to TEMPLATE_DIR so external task paths don't leak in arbitrary
# LAUNCH.mds from the filesystem. Every task must ship a LAUNCH.md;
# there is no generic fallback.
def _find_bundled_launch_md(task_path, template_dir):
    p = Path(task_path).resolve()
    template = Path(template_dir).resolve()
    if template != p and template not in p.parents:
        # External task: only consider its own LAUNCH.md
        cand = p / "LAUNCH.md"
        return cand if cand.exists() else None
    # Bundled: walk up, stopping before template_dir itself
    while p != template and p != p.parent:
        cand = p / "LAUNCH.md"
        if cand.exists():
            return cand
        p = p.parent
    return None

_task_launch_md = _find_bundled_launch_md(_task_path, TEMPLATE_DIR)
if not _task_launch_md:
    print(f"ERROR: no LAUNCH.md found by walking up from {_task_path}.")
    print(f"  Every task must ship a LAUNCH.md (the task-profile that fills the")
    print(f"  hooks referenced by runbook.md). See task-autoresearch/LAUNCH.md,")
    print(f"  task-biomlbench/LAUNCH.md, or task-protein-gym/LAUNCH.md as references.")
    sys.exit(1)
shutil.copy2(_task_launch_md, RUN_DIR / "task-profile.md")
print(f"  Copied: {_task_launch_md.relative_to(TEMPLATE_DIR)} → task-profile.md")

if IS_BENCHMARK:
    print(f"  To run: open {RUN_DIR}/{program_file} in a Claude Code session and follow it.")

# ── Protein substitution ─────────────────────────────────────
# If --protein is given, rewrite the placeholder protein name in task/*.md files.
# The task template uses "SPIKE_SARS2_Starr_2020_binding" as the default protein;
# this replaces it with the user-specified protein so agents know exactly what to work on.
PROTEIN_PLACEHOLDER = "SPIKE_SARS2_Starr_2020_binding"  # default in template
PROTEIN_TARGET = _args.protein  # None means no substitution
if PROTEIN_TARGET and PROTEIN_TARGET != PROTEIN_PLACEHOLDER:
    task_run_dir = RUN_DIR / "task"
    md_files = list(task_run_dir.glob("**/*.md"))
    substituted = []
    for md_path in md_files:
        text = md_path.read_text()
        if PROTEIN_PLACEHOLDER in text:
            md_path.write_text(text.replace(PROTEIN_PLACEHOLDER, PROTEIN_TARGET))
            substituted.append(md_path.name)
    if substituted:
        print(f"  Protein substitution: {PROTEIN_PLACEHOLDER} → {PROTEIN_TARGET}")
        print(f"    Updated: {', '.join(substituted)}")
    else:
        print(f"  NOTE: --protein specified but '{PROTEIN_PLACEHOLDER}' not found in any task/*.md")
elif PROTEIN_TARGET:
    print(f"  Protein: {PROTEIN_TARGET} (matches template default, no substitution needed)")

# ── Baseline table substitution ──────────────────────────────
# If the task directory has a baselines.csv, substitute {{BASELINE_TABLE}},
# {{BASELINE_NOTE}}, and {{SOTA_TABLE}} in task/TASK.md with protein-specific numbers.
import csv as _csv

def _load_baselines(task_src_dir, protein):
    """Return row dict for protein from baselines.csv, or None if not found."""
    csv_path = TEMPLATE_DIR / task_src_dir / "baselines.csv"
    if not csv_path.exists():
        return None
    with open(csv_path) as f:
        for row in _csv.DictReader(f):
            if row["protein"] == protein:
                return row
    return None

def _build_baseline_substitutions(row, protein):
    """Build the markdown strings to substitute into TASK.md/LAUNCH.md from a baselines.csv row."""
    c_pub = row["fold_contiguous_5_published"]
    m_pub = row["fold_modulo_5_published"]
    r_pub = row["fold_random_5_published"]
    c_our = row.get("fold_contiguous_5_repo_kermut") or row.get("fold_contiguous_5_ours", "")
    m_our = row.get("fold_modulo_5_repo_kermut") or row.get("fold_modulo_5_ours", "")
    r_our = row.get("fold_random_5_repo_kermut") or row.get("fold_random_5_ours", "")

    has_ours = c_our and m_our and r_our
    pub_mean = round((float(c_pub) + float(m_pub) + float(r_pub)) / 3, 4)

    if has_ours:
        our_mean = round((float(c_our) + float(m_our) + float(r_our)) / 3, 4)
        baseline_table = (
            f"| Split | Our reproduction | Published Kermut ({protein}) |\n"
            f"|---|---|---|\n"
            f"| `fold_contiguous_5` | {c_our} | {c_pub} |\n"
            f"| `fold_modulo_5` | {m_our} | {m_pub} |\n"
            f"| `fold_random_5` | {r_our} | {r_pub} |\n"
            f"| **Mean across splits** | **{our_mean}** | **~{pub_mean}** |"
        )
        baseline_note = "Results are within single-seed variance of the official published numbers."
        primary_metric_line = (
            f"`mean_spearman` baseline: **{our_mean}** (our reproduction). "
            f"Published Kermut: **~{pub_mean}**. Goal: beat both."
        )
        sota_last_line = (
            f"Our baseline (`kermut.py`) reproduces Kermut at {c_our} (fold_contiguous_5) "
            f"— within single-seed variance of the {c_pub} published number. "
            f"The goal is to improve beyond this."
        )
    else:
        # No reproduction yet — show published numbers only
        baseline_table = (
            f"| Split | Our reproduction | Published Kermut ({protein}) |\n"
            f"|---|---|---|\n"
            f"| `fold_contiguous_5` | *(not yet run)* | {c_pub} |\n"
            f"| `fold_modulo_5` | *(not yet run)* | {m_pub} |\n"
            f"| `fold_random_5` | *(not yet run)* | {r_pub} |\n"
            f"| **Mean across splits** | *(not yet run)* | **~{pub_mean}** |"
        )
        baseline_note = (
            "Reproduction not yet run for this protein. "
            "The first agent to run the baseline script should record results in "
            "the task's `baselines.csv` so future launches have accurate numbers."
        )
        primary_metric_line = (
            f"`mean_spearman` baseline: *(not yet run)*. "
            f"Published Kermut mean: **~{pub_mean}**. Goal: beat it."
        )
        sota_last_line = (
            f"Published Kermut score for this protein: {c_pub} (fold_contiguous_5). "
            f"Run `kermut.py {protein} fold_contiguous_5` to establish a reproduction baseline."
        )

    sota_table = (
        f"Benchmark results for `{protein}` on `fold_contiguous_5`\n"
        f"(source: `ProteinGym/benchmarks/DMS_supervised/substitutions/Spearman/"
        f"DMS_substitutions_Spearman_DMS_level_fold_contiguous_5.csv`):\n\n"
        f"| Model | Spearman | Type |\n"
        f"|-------|----------|------|\n"
        f"| **Kermut** (NeurIPS 2024) | **{c_pub}** | Composite GP: structure + sequence |\n"
        f"| ProteinNPT | 0.561 | Non-parametric transformer |\n"
        f"| Tranception Embeddings | 0.497 | Autoregressive LM |\n"
        f"| MSA Transformer Embeddings | 0.451 | MSA-based |\n"
        f"| ESM-1v Embeddings | 0.136 | Protein LM |\n\n"
        f"{sota_last_line}"
    )

    return baseline_table, baseline_note, primary_metric_line, sota_table


def _apply_baseline_substitutions(md_path, bt, bn, pml, st, protein):
    """Apply all baseline placeholders plus {PROTEIN} into a markdown file."""
    if not md_path.exists():
        return
    text = md_path.read_text()
    if not any(p in text for p in ("{{BASELINE_TABLE}}", "{{SOTA_TABLE}}", "{PROTEIN}")):
        return
    text = text.replace("{{BASELINE_TABLE}}", bt)
    text = text.replace("{{BASELINE_NOTE}}", bn)
    text = text.replace("{{PRIMARY_METRIC_LINE}}", pml)
    text = text.replace("{{SOTA_TABLE}}", st)
    text = text.replace("{PROTEIN}", protein)
    md_path.write_text(text)


_effective_protein = PROTEIN_TARGET or PROTEIN_PLACEHOLDER
_baseline_row = _load_baselines(task_source, _effective_protein)

if _baseline_row:
    _bt, _bn, _pml, _st = _build_baseline_substitutions(_baseline_row, _effective_protein)
    _apply_baseline_substitutions(
        RUN_DIR / "task" / "TASK.md", _bt, _bn, _pml, _st, _effective_protein
    )
    _apply_baseline_substitutions(
        RUN_DIR / "task" / "LAUNCH.md", _bt, _bn, _pml, _st, _effective_protein
    )
    print(f"  Baseline table: substituted from baselines.csv for {_effective_protein}")
else:
    _task_md_path = RUN_DIR / "task" / "TASK.md"
    if _task_md_path.exists() and "{{BASELINE_TABLE}}" in _task_md_path.read_text():
        print(f"  WARNING: {{{{BASELINE_TABLE}}}} placeholder in TASK.md but no baselines.csv "
              f"entry for '{_effective_protein}' — placeholders left unresolved")

for f in ["README.md", ".gitignore"]:
    src = TEMPLATE_DIR / f
    if src.exists():
        shutil.copy2(src, RUN_DIR / f)

# Copy .key into ablation so agents can use it
if (TEMPLATE_DIR / ".key").exists():
    shutil.copy2(TEMPLATE_DIR / ".key", RUN_DIR / ".key")
    (RUN_DIR / ".key").chmod(0o600)

# ── repo/ and champion/ setup ────────────────────────────────
#
# Three cases, determined by what's in the task source directory:
#
#   1. biomlbench (IS_BENCHMARK): no repo/, no champion/ — agents write from scratch
#   2. Task-bundled repo (task dir has its own repo/ and champion/) — copy
#      those, skip the karpathy clone entirely
#   3. Default (autoresearch): clone the upstream URL hardcoded below, seed champion/train.py
#
import json as _json
import subprocess as _subprocess

_task_has_repo = (TEMPLATE_DIR / task_source / "repo").is_dir()
_task_has_champion = (TEMPLATE_DIR / task_source / "champion").is_dir()

if IS_BENCHMARK:
    # Agents build their own solution; nothing to pre-populate.
    pass

elif _task_has_repo:
    # Copy the task's own repo/ into the run (not a shared mutable reference).
    _repo_src = TEMPLATE_DIR / task_source / "repo"
    shutil.copytree(_repo_src, RUN_DIR / "repo", symlinks=False,
                    ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc"))
    print(f"  Copied: repo/ <- {task_source}/repo/")

    # Copy the task's own champion/ if present.
    if _task_has_champion:
        _champ_src = TEMPLATE_DIR / task_source / "champion"
        shutil.copytree(_champ_src, RUN_DIR / "champion", symlinks=False,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  Copied: champion/ <- {task_source}/champion/")
    else:
        (RUN_DIR / "champion").mkdir(exist_ok=True)

else:
    # Default: create an ISOLATED, VERIFIED-PRISTINE repo/ via git clone.
    #
    # Prior bug: repo/ used to be a symlink to a shared directory that accumulated
    # working-tree modifications across runs. A new run would inherit ~1800 lines
    # of uncommitted code from whatever the previous run left behind, and measure
    # its "baseline" against that stale state. Every subsequent experiment was
    # then anchored to a non-upstream starting point. To prevent this, every run
    # now gets a fresh git clone of its upstream repo — no shared mutable state.
    #
    # Upstream URL + compat patches for the autoresearch benchmark.
    _upstream_cfg = {
        "url": "https://github.com/karpathy/autoresearch.git",
        "depth": 1,
        "compat_patches": [
            # glibc 2.28 systems can't load varunneal/flash-attention-3 (needs 2.32).
            # Fall back to kernels-community/flash-attn3 which works everywhere.
            {
                "file": "train.py",
                "find": 'repo = "varunneal/flash-attention-3" if cap == (9, 0) else "kernels-community/flash-attn3"',
                "replace": 'repo = "kernels-community/flash-attn3"  # glibc compat — varunneal FA3 needs 2.32',
            },
        ],
    }

    _repo_url = _upstream_cfg.get("url")
    if _repo_url:
        _clone_dst = RUN_DIR / "repo"
        _clone_cmd = ["git", "clone"]
        if _upstream_cfg.get("depth"):
            _clone_cmd += ["--depth", str(_upstream_cfg["depth"])]
        _clone_cmd += [_repo_url, str(_clone_dst)]
        _r = _subprocess.run(_clone_cmd, stdout=_subprocess.PIPE, stderr=_subprocess.PIPE)
        if _r.returncode != 0:
            print(f"  ERROR: clone of {_repo_url} failed: {(_r.stderr or b'').decode(errors='replace').strip()}")
            sys.exit(1)
        print(f"  Cloned: repo/ <- {_repo_url}")

        # Apply compat patches (e.g. kernel-fallback for glibc 2.28 hosts)
        for _p in _upstream_cfg.get("compat_patches", []):
            _target = _clone_dst / _p["file"]
            if _target.exists():
                _txt = _target.read_text()
                if _p["find"] in _txt:
                    _target.write_text(_txt.replace(_p["find"], _p["replace"]))
                    print(f"    Patched: {_p['file']} (compat)")
                else:
                    print(f"    WARN: patch-find string not present in {_p['file']}")
    elif (TEMPLATE_DIR / "repo").is_dir() or (TEMPLATE_DIR / "repo").is_symlink():
        # Legacy fallback: copy (NOT symlink) the template's repo into the run.
        repo_src = TEMPLATE_DIR / "repo"
        if repo_src.is_symlink():
            repo_src = repo_src.resolve()
        shutil.copytree(repo_src, RUN_DIR / "repo", symlinks=False,
                        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc"))
        print(f"  Copied: repo/ from {repo_src} (legacy fallback)")
    else:
        print(f"  NOTE: No repo/ source found in template. Agents will work from scratch.")

    (RUN_DIR / "champion").mkdir(exist_ok=True)

    # Seed champion/ with the fresh repo's train.py so agents have a real baseline
    # to copy from on their first cycle. Without this, champion/train.py starts
    # empty and the first agent has to discover where the code lives.
    _repo_train = RUN_DIR / "repo" / "train.py"
    if _repo_train.exists():
        shutil.copy2(_repo_train, RUN_DIR / "champion" / "train.py")
        (RUN_DIR / "champion" / "SOURCE").write_text(
            f"pristine upstream clone, populated {datetime.now(timezone.utc).isoformat()}\n"
        )
        print(f"  Seeded: champion/train.py <- repo/train.py (pristine)")

# Symlink .cache (shared training caches)
cache_src = TEMPLATE_DIR / ".cache"
if cache_src.is_symlink():
    os.symlink(os.readlink(cache_src), RUN_DIR / ".cache")
elif cache_src.is_dir():
    os.symlink(cache_src, RUN_DIR / ".cache")
else:
    for cache_dir in [".cache/uv", ".cache/huggingface", ".cache/torch"]:
        (RUN_DIR / cache_dir).mkdir(parents=True, exist_ok=True)

# Create runtime directories inside ablation
(RUN_DIR / "logs" / "raw").mkdir(parents=True, exist_ok=True)

print(f"  Copied: system/, task/ (from {task_source}), {program_file}")
if (RUN_DIR / "repo").exists():
    print(f"  Repo:   {RUN_DIR / 'repo'}")
print(f"  Linked: .cache/")
_created = ["logs/"]
if (RUN_DIR / "champion").exists():
    _created.append("champion/")
print(f"  Created: {', '.join(_created)}")

# ── All paths now point into the ablation directory ─────────

ROOT = RUN_DIR
REPO_DIR = ROOT / "repo"
# Always use "task" subdirectory in run directory
TASK_DIR = ROOT / "task"
AGENTS_DIR = ROOT / "agents"

# ── Run ID & naming ────────────────────────────────────────

RUN_ID = RUN_NAME.replace("-", "_").replace(".", "")

# Workshop name: use env var if set, otherwise default to ar_{RUN_ID}
if os.environ.get("WORKSHOP_NAME"):
    WORKSHOP_NAME = os.environ["WORKSHOP_NAME"].replace("-", "_")
    DISPLAY_NAME = os.environ.get("WORKSHOP_DISPLAY_NAME", WORKSHOP_NAME)
    DESCRIPTION = os.environ.get("WORKSHOP_DESCRIPTION", f"Multi-agent focus area — run {RUN_ID}")
else:
    WORKSHOP_NAME = f"ar_{RUN_ID}".replace("-", "_")
    DISPLAY_NAME = f"Autoresearch: {RUN_ID}"
    DESCRIPTION = f"Multi-agent optimization — run {RUN_ID}"

# Agent prefix
PREFIX = RUN_ID
if len(PREFIX) > 16:
    PREFIX = PREFIX[:6] + PREFIX[-10:]

# Agent roster: name -> (description, role, server, gpu)
AGENTS = {
    f"{PREFIX}_monitor":    ("Focus area monitor — bootstraps, forms teams, monitors health",   "monitor",   "server1", -1),
    f"{PREFIX}_gpu1":     ("GPU agent 1 — runs experiments on GPU 0",                       "gpu",     "server1",  0),
    f"{PREFIX}_gpu2":     ("GPU agent 2 — runs experiments on GPU 1",                       "gpu",     "server1",  1),
    f"{PREFIX}_gpu3":     ("GPU agent 3 — runs experiments on GPU 0",                       "gpu",     "server2",  0),
    f"{PREFIX}_gpu4":     ("GPU agent 4 — runs experiments on GPU 1",                       "gpu",     "server2",  1),
    f"{PREFIX}_gpu5":     ("GPU agent 5 — runs experiments on GPU 0",                       "gpu",     "server3",  0),
    f"{PREFIX}_gpu6":     ("GPU agent 6 — runs experiments on GPU 1",                       "gpu",     "server3",  1),
    f"{PREFIX}_analyst1": ("Analyst 1 — researches mechanisms, proposes experiments",        "analyst", "server1", -1),
    f"{PREFIX}_analyst2": ("Analyst 2 — researches mechanisms, proposes experiments",        "analyst", "server2", -1),
    f"{PREFIX}_analyst3": ("Analyst 3 — researches mechanisms, proposes experiments",        "analyst", "server3", -1),
}

NOW = datetime.now(timezone.utc).isoformat()


def setup_agent(name, desc, role, server, gpu):
    """Create local agent directory with credentials, AGENT.md, memory/."""
    agent_dir = AGENTS_DIR / name
    (agent_dir / "workspace" / "repo").mkdir(parents=True, exist_ok=True)
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)

    # Register on AnonAPI API
    r = requests.post(f"{API}/agents/register", headers=HEADERS, json={
        "name": name, "description": desc
    })
    token = None
    if r.status_code < 300:
        data = r.json()
        token = data.get("agent", {}).get("api_key")
        if token:
            print(f"  Registered: {name} (got unique token)")
        else:
            print(f"  Registered: {name} (no token in response, using shared)")
    else:
        print(f"  {name}: {r.status_code} (may already exist)")

    if not token:
        token = ADMIN_TOKEN

    # credentials.json
    creds_path = agent_dir / "credentials.json"
    if not creds_path.exists():
        creds_path.write_text(json.dumps({"api_key": token, "agent_name": name}, indent=2))
        creds_path.chmod(0o600)

    # AGENT.md — the agent's identity file (like CLAUDE.md)
    agent_md_path = agent_dir / "AGENT.md"
    if not agent_md_path.exists():
        gpu_line = f"GPU agent on GPU {gpu}." if role == "gpu" else f"{role.title()} agent."
        agent_md_path.write_text(f"""---
name: {name}
role: {role}
team: null
gpu: {gpu}
server: {server}
last_seen: null
status: idle
session_count: 0
last_experiment: null
last_outcome: null
last_val_bpb: null
---

# {name}

{gpu_line}

## Current Focus
(not yet assigned to a team)

## Suggestions for System Improvement
(none yet)

## Notes for Next Session
(none yet)
""")

    # memory/MEMORY.md — empty index
    memory_index = agent_dir / "memory" / "MEMORY.md"
    if not memory_index.exists():
        memory_index.write_text("# Memory Index\n\n(no memories yet)\n")

    # Build HEARTBEAT.md for this agent (self-contained: boot + role + team + record + exit)
    system_dir = TEMPLATE_DIR / "system" / "templates"
    heartbeat_template = (system_dir / "HEARTBEAT.md").read_text()

    # Inject role-specific content
    role_file_map = {
        "gpu": "ROLE-GPU.md",
        "analyst": "ROLE-ANALYST.md",
        "monitor": "ROLE-MONITOR.md",
    }
    role_src = system_dir / role_file_map.get(role, "ROLE-GPU.md")
    role_content = role_src.read_text() if role_src.exists() else ""
    # Strip frontmatter from role doc (already in heartbeat)
    role_parts = role_content.split("---")
    if len(role_parts) >= 3:
        role_content = "---".join(role_parts[2:]).strip()

    team_src = system_dir / "ROLE-TEAM.md"
    team_content = ""
    if team_src.exists():
        team_content = team_src.read_text()
        team_parts = team_content.split("---")
        if len(team_parts) >= 3:
            team_content = "---".join(team_parts[2:]).strip()

    # Replace placeholders
    heartbeat = heartbeat_template
    heartbeat = heartbeat.replace(
        "<!-- ROLE_CONTENT_PLACEHOLDER -->\n<!-- launch.py replaces this with system/templates/ROLE-{role}.md -->",
        role_content
    )
    heartbeat = heartbeat.replace(
        "<!-- TEAM_CONTENT_PLACEHOLDER -->\n<!-- launch.py replaces this with system/templates/ROLE-TEAM.md -->",
        team_content
    )

    (agent_dir / "HEARTBEAT.md").write_text(heartbeat)

    # Copy training repo for GPU agents (optional - only if repo exists)
    if role == "gpu":
        dst = agent_dir / "workspace" / "repo"
        repo_source = None

        # Pattern 1: Autoresearch - repo symlink at run root
        if REPO_DIR.exists() and REPO_DIR.is_symlink():
            repo_source = REPO_DIR
        # Pattern 2: Bio tasks - task/repo-* directory (e.g., task/repo-caco2/)
        else:
            task_repos = list(TASK_DIR.glob("repo-*"))
            if task_repos:
                repo_source = task_repos[0]

        # Copy baseline code to agent workspace if found
        if repo_source:
            shutil.copytree(repo_source, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('.venv', '__pycache__', '*.pyc', '.git'))
            print(f"    Copied {repo_source.name} -> {name}/workspace/repo/")

    return token


def put_file(ws_id, path, content):
    """Write a file to the workspace."""
    r = requests.put(f"{API}/workspaces/{ws_id}/files/{path}",
                     headers=HEADERS, json={"content": content})
    status = "OK" if r.status_code < 300 else f"ERR {r.status_code}"
    print(f"  {path}: {status}")


def main():
    print()
    print("=" * 60)
    print("  Autoresearch Focus Area — Fresh Launch")
    print(f"  Template:   {TEMPLATE_DIR}")
    print(f"  Experiment: {ROOT}")
    print(f"  Run ID:     {RUN_ID}")
    print(f"  Workshop:   {WORKSHOP_NAME}")
    print(f"  Prefix:     {PREFIX}")
    print("=" * 60)

    # ── Step 1: Create NEW Workshop ─────────────────────────
    print(f"\n[1/6] Creating workshop: {WORKSHOP_NAME}")
    r = requests.post(f"{API}/workshops", headers=HEADERS, json={
        "name": WORKSHOP_NAME,
        "display_name": DISPLAY_NAME,
        "description": DESCRIPTION,
        "instructions": (
            "Multi-agent focus area for benchmark optimization.\n\n"
            "Post types: [PROPOSAL], [RESULT], [DISCUSSION], [NEAR-MISS], [AUDIT]\n\n"
            "Rules:\n"
            "- Discussion before queuing: post [PROPOSAL] and get 1+ comment first\n"
            "- One change per experiment: apply champion config, then ONE modification\n"
            "- Results are write-once: never overwrite\n"
        ),
    })
    if r.status_code < 300:
        print(f"  Created: {WORKSHOP_NAME}")
    else:
        print(f"  Error: {r.status_code} — {r.text[:200]}")
        sys.exit(1)

    # ── Step 2: Register Agents + Create Directories ────────
    print(f"\n[2/6] Setting up {len(AGENTS)} agents...")
    agent_tokens = {}
    for name, (desc, role, server, gpu) in AGENTS.items():
        token = setup_agent(name, desc, role, server, gpu)
        agent_tokens[name] = token

    # ── Step 3: Subscribe Agents ────────────────────────────
    # Subscribe must include X-Agent-Name so the server knows WHICH agent is
    # subscribing — otherwise it returns {subscribed: false} silently and
    # workshop.subscriber_count stays at 0.
    print(f"\n[3/6] Subscribing agents to {WORKSHOP_NAME}...")
    subscribed = 0
    for name in AGENTS:
        r = requests.post(
            f"{API}/workshops/{WORKSHOP_NAME}/subscribe",
            headers={**HEADERS, "X-Agent-Name": name},
        )
        if r.status_code < 300:
            action = r.json().get("action", "")
            if action in ("subscribed", "already_subscribed"):
                subscribed += 1
            else:
                print(f"  Warning: {name} subscribe returned {r.status_code} {r.text[:120]}")
        else:
            print(f"  Warning: {name} subscribe returned {r.status_code} {r.text[:120]}")
    print(f"  Subscribed {subscribed}/{len(AGENTS)} agents")

    # ── Step 4: Create Main Workspace ───────────────────────
    print("\n[4/6] Creating main workspace...")
    ws = requests.post(f"{API}/workspaces", headers=HEADERS, json={
        "title": f"{WORKSHOP_NAME}-coordination",
        "description": "Main coordination workspace — champion, results, knowledge, teams",
        "workshop": WORKSHOP_NAME,
        "visibility": "public"
    })
    if ws.status_code >= 300:
        print(f"  Error: {ws.status_code} — {ws.text[:200]}")
        sys.exit(1)
    ws_id = ws.json()["id"]
    print(f"  Workspace ID: {ws_id}")

    # Save workspace ID and run metadata locally
    (ROOT / "WORKSPACE_ID").write_text(ws_id)
    (ROOT / "WORKSHOP_NAME").write_text(WORKSHOP_NAME)
    (ROOT / "run_metadata.json").write_text(json.dumps({
        "run_id": RUN_ID,
        "workshop": WORKSHOP_NAME,
        "workspace_id": ws_id,
        "prefix": PREFIX,
        "api": API,
        "created_at": NOW,
        "template": str(TEMPLATE_DIR),
        "ablation": str(ROOT),
        "protein": PROTEIN_TARGET or PROTEIN_PLACEHOLDER,
        "task_type": _task_type,
        "agents": list(AGENTS.keys())
    }, indent=2))
    print(f"  Saved WORKSPACE_ID, WORKSHOP_NAME, and run_metadata.json")

    # ── Step 5: Populate Workspace ──────────────────────────
    print("\n[5/6] Populating workspace...")

    task_md, _task_md_source = _read_task_md(TASK_DIR)
    if _task_md_source != "TASK.md":
        print(f"  Note: Using {_task_md_source} (no TASK.md found)")
    put_file(ws_id, "task.md", task_md)

    put_file(ws_id, "champion.md", f"""---
metric: null
run_id: null
agent: null
updated_at: "{NOW}"
status: awaiting_baseline
settings: {{}}
---

# Champion Configuration

No champion yet. The first agent to run the baseline will establish it.
Check task/TASK.md for the optimization metric and baseline instructions.
""")

    put_file(ws_id, "knowledge/patterns.md", f"""---
version: 1
updated_at: "{NOW}"
---

# Winning Patterns

(none yet — to be discovered)

# Dead Ends

| Axis | Best Delta | Why |
|---|---|---|

# Load-Bearing Parameters

(to be identified)
""")

    put_file(ws_id, "knowledge/exhausted.md", f"""---
count: 0
updated_at: "{NOW}"
---

# Exhausted Axes

(none yet)
""")

    put_file(ws_id, "teams/roster.md", f"""---
teams: {{}}
updated_at: "{NOW}"
phase: planning
---

# Team Roster

Teams formed during Phase 2 discussion.
""")

    for name, (desc, role, server, gpu) in AGENTS.items():
        put_file(ws_id, f"agents/{name}.md",
                 f"---\nagent: {name}\nrole: {role}\nserver: {server}\ngpu: {gpu}\n"
                 f"status: idle\nlast_seen: null\nteam: null\n---\n")

    # ── Step 6: Post Kickoff ────────────────────────────────
    print("\n[6/6] Posting kickoff discussion...")

    # Read task definition to generate kickoff content
    import yaml
    task_content, _ = _read_task_md(TASK_DIR)
    task_parts = task_content.split("---")
    if len(task_parts) >= 3:
        task_meta = yaml.safe_load(task_parts[1]) or {}
        task_name = task_meta.get("name", "optimization")
        task_desc = task_meta.get("description", "Optimize the benchmark metric.")
    else:
        task_name = "optimization"
        task_desc = "Optimize the benchmark metric."

    # Extract first section after frontmatter as the problem description
    task_body = "---".join(task_parts[2:]) if len(task_parts) >= 3 else task_content
    problem_section = task_body.split("\n## ")[0].strip()  # First section before next ##

    kickoff = requests.post(f"{API}/posts", headers=HEADERS, json={
        "submolt": WORKSHOP_NAME,
        "title": "[DISCUSSION-TRIGGER] Cold-start bootstrap — form hypothesis-based teams",
        "content": f"""# Cold-Start Bootstrap

This post anchors the cold-start self-regroup: every agent that runs
before a roster is committed should contribute to this thread, then
the alphabetically-last analyst who participates writes
`teams/roster.md` per Step 0.25 of ROLE-ANALYST.

## The Task

{task_desc}

**Full task definition:** `task/TASK.md` in the workspace
**Negative-knowledge file (if shipped with task):** `task/EXPLORED.md`

{problem_section[:500]}...

## What each agent contributes

- **Dimension / hypothesis** you want the team structure to target. Describe
  it with `hypothesis / prediction / falsification` (see ROLE-ANALYST Step 0.3).
- **≥1 cold axis** per team (an axis with zero prior experiments in the
  workspace — see ROLE-ANALYST Step 0.25 cold-axis mandate).
- **Substantive new content per round**: a [GAPS]/[CONSTANTS]/[RANKED]
  post or comment that adds information nobody else has surfaced.
- **Cast a self-termination vote**: `[DISCUSS-MORE]` or `[DISCUSS-DONE]`
  as a comment ON THIS POST. Reform closes when ≥5 agents vote
  `[DISCUSS-DONE]`.

## How team reform closes

Per ROLE-ANALYST Step 0.25: when 5+ `[DISCUSS-DONE]` votes land, the
alphabetically-last analyst who has run in this rotation writes
`teams/roster.md` with 3 hypothesis-based teams (each with ≥1 cold
axis) and posts `[TEAM-REFORMED]`. This closes bootstrap.

No monitor intervention is required.

**Workspace:** `{ws_id}`
**System docs:** `system/reference/SKILL.md`, `system/reference/PHASES.md`, `task/TASK.md`
""",
        "notify_agents": list(AGENTS.keys()),
        "tags": ["phase:planning", "type:discussion-trigger", "cold-start"]
    })

    if kickoff.status_code < 300:
        post_id = kickoff.json().get("post", {}).get("id", "unknown")
        print(f"  [DISCUSSION-TRIGGER] post: {post_id}")
    else:
        print(f"  Warning: {kickoff.status_code} — {kickoff.text[:200]}")

    # Save agent tokens
    tokens_path = ROOT / "agent_tokens.json"
    with open(tokens_path, "w") as f:
        json.dump(agent_tokens, f, indent=2)

    # ── Done ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  Launch complete!")
    print("=" * 60)
    program_file = "runbook.md"
    task_type_label = "biomlbench" if IS_BENCHMARK else ("proteingym" if IS_PROTEINGYM else "optimization")
    print(f"""
  Experiment dir: {ROOT}
  Workshop:      {WORKSHOP_NAME}
  Workspace ID:  {ws_id}
  Agents:        {len(AGENTS)} created in {ROOT / 'agents'}
  Task type:     {task_type_label}

  To run the orchestrator:

    claude -p "Read {ROOT / program_file} and execute"
""")


if __name__ == "__main__":
    main()
