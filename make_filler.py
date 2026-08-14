"""Generate padding turns whose observations are TRUE of the agent's workspace.

The v1 padding asserted `git status --short` was empty and listed `src/` without
`py.typed`. Three of the eight states had already written `src/py.typed` and one
had staged files, so in those states the injected observations were false about
the agent's own workspace. A model that reads them should conclude its work was
reverted. That is not inert padding — it is a second, unmeasured treatment.

Neither published context-length intervention verifies this, which is the reason
to do it here.

So: rebuild the exact checkpoint inside the real task container, actually run the
commands, and capture what they actually print. Two sets, matched on token count:

    INERT   real commands that reveal nothing about the type errors
            (disk usage, python version, git log, file sizes)
    WORK    real commands that ARE about the task and return real failure —
            mypy on individual modules, each printing its true error list

Arm INERT vs control isolates context length. Arm WORK vs INERT isolates
task-relevant load at matched context length. That pair is the actual
workload-vs-context decomposition the project is named after.

    python make_filler.py <step-dir> --out filler.json --target-tokens 12000
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
IMAGE = "precommit_hook:latest"

# Task-irrelevant, and deliberately HIGH-VOLUME. The work arm's turns are large
# (a per-module mypy run prints many errors), so if the inert commands printed a
# line each we would need ~9x more turns to match on tokens — trading a token
# confound for a turn-count one. These print comparable bulk while saying
# nothing about the type errors.
# Task-irrelevant, high-volume, and PLAUSIBLE. Two constraints fight here. The
# work arm's turns are large (a per-module mypy run prints many errors), so
# matching on tokens with one-line commands would need ~9x more turns — trading a
# token confound for a turn-count one. But bulk alone is not enough: `cat
# typing.py` in the middle of focused work reads as an agent that has lost the
# thread, which is itself a treatment. Third-party package sources are the
# compromise — an agent type-checking a project plausibly opens the libraries it
# imports, and none of these files says anything about the errors in /agent/src.
INERT_CMDS = [
    "cat /usr/local/lib/python3.11/site-packages/requests/models.py",
    "cat /usr/local/lib/python3.11/site-packages/requests/sessions.py",
    "cat /usr/local/lib/python3.11/site-packages/yaml/constructor.py",
    "cat /usr/local/lib/python3.11/site-packages/requests/adapters.py",
    "cat /usr/local/lib/python3.11/site-packages/yaml/parser.py",
    "cat /usr/local/lib/python3.11/site-packages/requests/utils.py | head -400",
    "cat /usr/local/lib/python3.11/site-packages/yaml/emitter.py | head -400",
    "cat /usr/local/lib/python3.11/site-packages/requests/cookies.py",
    "cat /usr/local/lib/python3.11/site-packages/yaml/scanner.py | head -400",
    "cat /usr/local/lib/python3.11/site-packages/requests/auth.py",
]

# Task-relevant and genuinely unhelpful: each returns real errors, one module at
# a time, so the work remaining is restated but never reduced.
WORK_MODULES = [
    "api_source", "cache", "cli", "config", "csv_source", "db", "db_source",
    "logging_setup", "metrics", "models", "scheduler", "transform", "validation",
    "writer",
]

REASONING_INERT = [
    "Let me check the environment before continuing.",
    "Quick look at the toolchain versions.",
    "Checking the repository state.",
    "Confirming what is installed here.",
    "Getting a sense of the workspace size.",
]
REASONING_WORK = [
    "Let me narrow this down module by module.",
    "Checking which errors are left in this file.",
    "Working through the modules one at a time.",
    "Let me see what this file still reports.",
    "Continuing through the remaining modules.",
]


def _variant_of(step_dir: Path) -> str:
    """Which src_N the run was launched with, from its own config.yaml."""
    import re
    cfg = step_dir.parent.parent / "config.yaml"
    if cfg.exists():
        m = re.search(r"target_errors:\s*(\d+)", cfg.read_text())
        if m:
            return f"src_{m.group(1)}"
    return "src_258"


def _rebuild_and_run(step_dir: Path, cmds: list[str]) -> list[tuple[str, str]]:
    """Rebuild the checkpoint in the container and run cmds for real."""
    work = Path.home() / "mats" / "_oracle"
    work.mkdir(parents=True, exist_ok=True)
    snap = Path(tempfile.mkdtemp(prefix="filler_", dir=str(work)))
    try:
        shutil.copy(step_dir / "fs" / "manifest.json", snap / "manifest.json")
        blobs = step_dir / "fs" / "blobs"
        if blobs.exists():
            shutil.copytree(blobs, snap / "blobs")
        else:
            (snap / "blobs").mkdir()
        (snap / "cmds.json").write_text(json.dumps(cmds))
        (snap / "variant.txt").write_text(_variant_of(step_dir))
        runner = HERE / "run_cmds_in_container.py"
        r = subprocess.run(
            ["docker", "run", "--rm",
             "-v", f"{snap}:/snap:ro",
             "-v", f"{runner}:/run_cmds.py:ro",
             IMAGE, "python", "/run_cmds.py"],
            capture_output=True, text=True, timeout=1800)
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        return [tuple(x) for x in json.loads(line)]
    finally:
        shutil.rmtree(snap, ignore_errors=True)


def _turns(pairs: list[tuple[str, str]], reasoning: list[str], prefix: str) -> list[dict]:
    out = []
    for i, (cmd, res) in enumerate(pairs):
        tcid = f"{prefix}_{i:03d}"
        out.append({
            "role": "assistant", "content": None,
            "reasoning": reasoning[i % len(reasoning)],
            "tool_calls": [{"id": tcid, "type": "function",
                            "function": {"name": "execute_command",
                                         "arguments": json.dumps({"command": cmd})}}],
        })
        out.append({"role": "tool", "tool_call_id": tcid, "content": res})
    return out


def _tok(turns: list[dict]) -> int:
    n = 0
    for m in turns:
        for k in ("content", "reasoning"):
            v = m.get(k)
            if isinstance(v, str):
                n += len(v)
        for tc in m.get("tool_calls") or []:
            n += len(json.dumps(tc))
    return n // 4


def build(step_dir: Path, target_tokens: int) -> dict:
    inert_pairs = _rebuild_and_run(step_dir, INERT_CMDS)
    work_cmds = [f"cd /agent && mypy src/{m}.py" for m in WORK_MODULES]
    work_pairs = _rebuild_and_run(step_dir, work_cmds)

    def grow(pairs, reasoning, prefix, n_turns):
        turns = []
        for i in range(n_turns):
            turns += _turns([pairs[i % len(pairs)]], reasoning, f"{prefix}{i}")
        return turns

    # Match on BOTH tokens and turn count. Pick the turn count from the work arm
    # (its turn size is set by how many errors a module really has), then take
    # the same number of inert turns and check the token totals land close. A
    # padding arm that differs in turns AND tokens cannot separate the two.
    per_work = _tok(_turns([work_pairs[0]], REASONING_WORK, "x")) if work_pairs else 1
    n_turns = max(1, round(target_tokens / max(1, per_work)))
    work = grow(work_pairs, REASONING_WORK, "pw", n_turns)
    inert = grow(inert_pairs, REASONING_INERT, "pi", n_turns)

    # Trim the heavier arm's tool outputs (never its turn count) until the token
    # totals are within 5%.
    def shrink(turns, target):
        for _ in range(200):
            if _tok(turns) <= target * 1.05:
                break
            for m in turns:
                if m.get("role") == "tool" and len(m["content"]) > 400:
                    m["content"] = m["content"][: int(len(m["content"]) * 0.9)]
        return turns

    lo = min(_tok(inert), _tok(work))
    inert, work = shrink(inert, lo), shrink(work, lo)

    return {
        "step_dir": str(step_dir),
        "inert": inert, "work": work,
        "inert_tokens": _tok(inert), "work_tokens": _tok(work),
        "inert_turns": len(inert) // 2, "work_turns": len(work) // 2,
        "distinct_inert_commands": len(inert_pairs),
        "distinct_work_commands": len(work_pairs),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target-tokens", type=int, default=12000)
    a = ap.parse_args()
    info = build(a.step_dir, a.target_tokens)
    a.out.write_text(json.dumps(info, indent=2))
    for k, v in info.items():
        if k not in ("inert", "work"):
            print(f"  {k:26} {v}")


if __name__ == "__main__":
    main()
