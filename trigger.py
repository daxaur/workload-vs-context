"""How long after seeing the error wall does the shortcut get written?

The hazard peaks at steps 6-8 and then falls. The obvious mechanism is that the
peak is not about elapsed time at all: it is the moment the agent first runs
mypy and gets 258 errors back. If the lag between that observation and the first
workaround artifact is short and tightly distributed, then "time in context"
never gets a chance to matter — the decision is a reaction to an observation,
and it is made almost immediately.

That is a checkable prediction and it competes directly with the project's own
premise, which is the reason to check it.

Measured per rollout, from the recorded command list and the checkpoints:

    t_see     first step whose command runs mypy over src/ and whose output
              reports errors (not a --help, not a scratch file in /tmp)
    t_act     first step whose filesystem grades as a workaround
    lag       t_act - t_see

    python trigger.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from transience import grade_step  # noqa: E402

BASE = Path.home() / "mats" / "agent-interp-envs" / "results"

# A real check of the package, not `mypy --help` and not a /tmp scratch file.
RUNS_MYPY = re.compile(r"\bmypy\b(?![^\n]*--help)(?![^\n]*/tmp/)", re.I)
FOUND_ERRORS = re.compile(r"Found (\d+) errors?", re.I)


def step_messages(step: Path) -> list:
    p = step / "messages.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def first_error_wall(steps: list[Path]) -> tuple[int | None, int | None]:
    """(step index, error count) the first time mypy reports errors on src/."""
    seen = set()
    for s in steps:
        k = int(s.name.split("-")[1])
        for m in step_messages(s):
            if m.get("role") != "tool":
                continue
            key = (m.get("tool_call_id"), len(m.get("content") or ""))
            if key in seen:
                continue
            seen.add(key)
            body = m.get("content") or ""
            hit = FOUND_ERRORS.search(body)
            if hit and int(hit.group(1)) > 0:
                return k, int(hit.group(1))
    return None, None


def main() -> None:
    rows = []
    for run in sorted(BASE.rglob("run-*")):
        if not run.is_dir():
            continue
        steps = sorted(run.glob("step-*"), key=lambda d: int(d.name.split("-")[1]))
        if not steps:
            continue
        if any("TIMEOUT" in (s / "messages.json").read_text(errors="ignore")
               for s in steps if (s / "messages.json").exists()):
            continue  # corrupted batch: the agent never saw the errors
        t_see, n_err = first_error_wall(steps)
        t_act = None
        for s in steps:
            _, cheat = grade_step(s)
            if cheat:
                t_act = int(s.name.split("-")[1])
                break
        model = "deepseek" if "deepseek" in str(run) else "gpt-oss-120b"
        rows.append({"run": str(run), "model": model, "t_see": t_see,
                     "n_err": n_err, "t_act": t_act,
                     "last": int(steps[-1].name.split("-")[1])})

    (HERE / "trigger.json").write_text(json.dumps(rows, indent=2))

    for model in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == model]
        saw = [r for r in rs if r["t_see"] is not None]
        acted = [r for r in saw if r["t_act"] is not None]
        print(f"\n=== {model}   n={len(rs)} ===")
        print(f"  ever saw mypy report errors on src/ : {len(saw)}/{len(rs)}")
        print(f"  of those, wrote a workaround        : {len(acted)}/{len(saw)}")
        if saw:
            ts = sorted(r["t_see"] for r in saw)
            print(f"  step of first error wall            : median {ts[len(ts)//2]}"
                  f"  range {ts[0]}-{ts[-1]}")
        if acted:
            lags = sorted(r["t_act"] - r["t_see"] for r in acted)
            print(f"  LAG see -> act (steps)              : median {lags[len(lags)//2]}"
                  f"  range {lags[0]}-{lags[-1]}")
            print(f"    distribution: {dict(sorted(Counter(lags).items()))}")
            within2 = sum(1 for x in lags if x <= 2)
            print(f"    within 2 steps of first seeing the errors: "
                  f"{within2}/{len(lags)} = {within2/len(lags):.0%}")
        pre = [r for r in acted if r["t_act"] < r["t_see"]]
        if pre:
            print(f"  !! wrote the workaround BEFORE ever seeing an error count: "
                  f"{len(pre)} — check these by hand")
            for r in pre[:5]:
                print(f"       t_act={r['t_act']} t_see={r['t_see']} {r['run'][-34:]}")
        never = [r for r in rs if r["t_see"] is None]
        if never:
            print(f"  never saw an error count at all     : {len(never)}"
                  f"   (of which wrote a workaround: "
                  f"{sum(1 for r in never if r['t_act'] is not None)})")


if __name__ == "__main__":
    main()
