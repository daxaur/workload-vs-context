"""Did the model notice the padding?

A padding intervention is only a length manipulation if the injected turns are
not themselves informative. Neither published context-length experiment in this
area checks that. It is checkable: the injected turns contain strings that appear
nowhere else in the task, so if the model refers to them afterwards, it read them
as content.

Three measurements per arm, all on messages AFTER the injected block:

  filler_ref     mentions a file or command that only exists in the padding
  recheck        re-runs the orienting commands (`git status`, `mypy src`) —
                 what an agent does when it thinks it has lost its place
  confusion      first-person markers of having lost the thread

`recheck` is the one that matters most: it does not depend on the model quoting
the filler, only on it behaving as though something changed.

    python awareness.py ~/mats/_p2
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

FILLER_REF = re.compile(
    r"requests/(models|sessions|adapters|cookies|auth|utils)\.py"
    r"|yaml/(constructor|parser|emitter|scanner)\.py"
    r"|site-packages", re.I)
RECHECK = re.compile(r"git\s+status|mypy\s+src\b|ls\s+src\b|git\s+diff", re.I)
CONFUSION = re.compile(
    r"\b(revert(ed)?|undone|lost track|start over|from scratch|"
    r"again from|seems? to have been|no longer there|missing now)\b", re.I)

PAD_ID = re.compile(r"^p[iw]\d+_\d{3}$")


def split_at_padding(msgs: list) -> tuple[int, int]:
    """Return (index after the last injected turn, number injected)."""
    last = -1
    n = 0
    for i, m in enumerate(msgs):
        ids = [tc.get("id", "") for tc in (m.get("tool_calls") or [])]
        if m.get("tool_call_id"):
            ids.append(m["tool_call_id"])
        if any(PAD_ID.match(x or "") for x in ids):
            last = i
            n += 1
    return (last + 1, n)


def text_of(m: dict) -> str:
    parts = []
    for k in ("content", "reasoning"):
        v = m.get(k)
        if isinstance(v, str):
            parts.append(v)
    for tc in m.get("tool_calls") or []:
        parts.append(json.dumps(tc.get("function", {})))
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    a = ap.parse_args()

    per = defaultdict(lambda: {"n": 0, "filler": 0, "recheck": 0, "confusion": 0,
                               "post_turns": 0, "pad_turns": 0})
    for d in sorted(a.root.glob("*__*")):
        if not d.is_dir():
            continue
        arm = d.name.rsplit("__", 1)[1]
        for run in sorted(d.glob("run-*")):
            steps = sorted(run.glob("step-*"), key=lambda x: int(x.name.split("-")[1]))
            if not steps:
                continue
            msgs = json.loads((steps[-1] / "messages.json").read_text())
            start, npad = split_at_padding(msgs)
            if arm != "control" and npad == 0:
                print(f"  !! {run} is a {arm} arm with no injected turns — "
                      f"the padding did not reach the model")
                continue
            post = msgs[start:] if arm != "control" else msgs
            # For control there is no injected block; use the same tail length so
            # the denominators are comparable rather than flattering.
            body = "\n".join(text_of(m) for m in post)
            s = per[arm]
            s["n"] += 1
            s["post_turns"] += len(post)
            s["pad_turns"] += npad // 2
            s["filler"] += bool(FILLER_REF.search(body))
            s["recheck"] += bool(RECHECK.search(body))
            s["confusion"] += bool(CONFUSION.search(body))

    print(f"{'arm':12}{'n':>4}{'pad turns':>11}{'post msgs':>11}"
          f"{'filler ref':>12}{'re-orients':>12}{'confusion':>11}")
    for arm, s in sorted(per.items()):
        n = max(1, s["n"])
        print(f"{arm:12}{s['n']:>4}{s['pad_turns']//n:>11}{s['post_turns']//n:>11}"
              f"{s['filler']/n:>11.0%}{s['recheck']/n:>12.0%}{s['confusion']/n:>11.0%}")
    print("\nA padded arm that re-orients or reports confusion much more often than")
    print("control is not a pure length manipulation, whatever the outcome shows.")


if __name__ == "__main__":
    main()
