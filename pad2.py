"""Build the three-arm padding condition from a live checkpoint.

v1 added 578 tokens — 4% of context — and its filler asserted things about the
workspace that were false in half the states. This fixes both, and adds the arm
that makes the comparison identify anything:

    CONTROL       resume the checkpoint as-is
    PAD_INERT     + N turns of REAL commands on the agent's REAL workspace that
                  say nothing about the type errors
    PAD_WORK      + N turns of REAL per-module mypy runs returning REAL errors,
                  matched to PAD_INERT on both token count and turn count

CONTROL -> PAD_INERT is context length with workload held fixed.
PAD_INERT -> PAD_WORK is task load at matched context length.

Everything else is identical by construction: same filesystem manifest, same
blobs, same `state.json`, so the same step budget and the same work remaining.
Verified per arm before anything is run.

    python pad2.py <step-dir> --filler filler.json --arm inert --out ~/mats/_p2/x
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def make(step_dir: Path, filler: dict, arm: str, out_dir: Path) -> dict:
    """Copy a checkpoint and splice one arm's turns into its conversation.

    The output must be shaped <root>/run-N/step-M/ with config.yaml at the root:
    resume.py resolves the run config as `step_dir/../../config.yaml`, and if it
    is missing Docker creates a directory at that path and the container dies
    with IsADirectoryError — reported only as "run failed".
    """
    root = out_dir
    if root.exists():
        shutil.rmtree(root)
    dest = root / "run-1" / step_dir.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(step_dir, dest)
    src_cfg = step_dir.parent.parent / "config.yaml"
    if src_cfg.exists():
        shutil.copy(src_cfg, root / "config.yaml")

    msgs = json.loads((dest / "messages.json").read_text())
    pad = json.loads(json.dumps(filler[arm]))  # deep copy

    # Insert immediately before the final assistant message. Depth is NOT varied
    # in this experiment — inserting mid-conversation would break the agent's own
    # references to its earlier outputs, which is a second treatment. So: one
    # placement, held constant, and the amount is what changes.
    insert_at = len(msgs)
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "assistant":
            insert_at = i
            break
    msgs[insert_at:insert_at] = pad
    (dest / "messages.json").write_text(json.dumps(msgs, indent=2))

    st = json.loads((dest / "state.json").read_text())
    return {
        "arm": arm,
        "step": st["step"],
        "padding_turns": len(pad) // 2,
        "padding_tokens": filler[f"{arm}_tokens"],
        "messages_before": len(msgs) - len(pad),
        "messages_after": len(msgs),
        "inserted_at": insert_at,
        "out": str(dest),
    }


def verify(orig: Path, padded: Path) -> list[str]:
    """Assert the intervention changed ONLY the conversation."""
    problems = []
    if json.loads((orig / "state.json").read_text()) != json.loads((padded / "state.json").read_text()):
        problems.append("state.json differs — budget or command log was altered")

    ma, mb = orig / "fs" / "manifest.json", padded / "fs" / "manifest.json"
    if ma.exists() != mb.exists():
        problems.append("manifest presence differs")
    elif ma.exists() and ma.read_bytes() != mb.read_bytes():
        problems.append("filesystem manifest differs — WORK REMAINING CHANGED")

    ba = sorted(p.name for p in (orig / "fs" / "blobs").glob("*")) if (orig / "fs" / "blobs").exists() else []
    bb = sorted(p.name for p in (padded / "fs" / "blobs").glob("*")) if (padded / "fs" / "blobs").exists() else []
    if ba != bb:
        problems.append("blob set differs — workspace content changed")

    oa = json.loads((orig / "messages.json").read_text())
    ob = json.loads((padded / "messages.json").read_text())
    if len(ob) <= len(oa):
        problems.append("no padding was inserted")
    it = iter(ob)
    if not all(any(m == o for m in it) for o in oa):
        problems.append("original conversation was not preserved in order")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step_dir", type=Path)
    ap.add_argument("--filler", type=Path, required=True)
    ap.add_argument("--arm", choices=["inert", "work", "repeat"], required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    filler = json.loads(a.filler.read_text())
    if filler["step_dir"] != str(a.step_dir):
        raise SystemExit(
            f"filler was generated from {filler['step_dir']}, not {a.step_dir} — "
            "the whole point is that the observations are true of THIS workspace")

    info = make(a.step_dir, filler, a.arm, a.out)
    padded = next((a.out / "run-1").glob("step-*"))
    problems = verify(a.step_dir, padded)
    for k, v in info.items():
        print(f"  {k:18} {v}")
    if problems:
        print("\n  x VERIFICATION FAILED")
        for p in problems:
            print(f"     {p}")
        raise SystemExit(1)
    print("\n  ok filesystem byte-identical · state.json identical · conversation extended")


if __name__ == "__main__":
    main()
