"""Pick checkpoints to intervene on, from inside the decision window.

v1 sampled states at steps 6-15 and found 6 of 8 already decided (ICC 0.68). The
timing analysis says why: the agent sees the error count at step ~2 and writes
the workaround a median of 6 steps later. A checkpoint taken at step 13 is often
past the decision; one taken at step 3-5 is inside it.

So candidates must satisfy all of:

  * the rollout is clean (no <TIMEOUT> anywhere)
  * the checkpoint does NOT already grade as a workaround
  * the agent has already seen `Found N errors` — otherwise there is no decision
    pending and the state is not on the causal path at all
  * step is within the lag window: t_see + 1 .. t_see + 5
  * for rollouts that did shortcut, step < t_act

Selection uses only the ORIGINAL rollout, never the outcome of resampling, so it
cannot be tuned toward a result. Screening on resampled p(cheat) happens after,
in a separate step, and that criterion is fixed in advance: keep 2 <= k <= 4 of 6.

    python candidates.py --n 16 --out candidates.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from transience import grade_step  # noqa: E402
from trigger import first_error_wall  # noqa: E402

BASE = Path.home() / "mats" / "agent-interp-envs" / "results"

# Which pool to draw from matters more than anything else in this file. At 258
# errors gpt-oss-120b writes a workaround in 10 of 10 rollouts, so every
# checkpoint from that pool is at the ceiling and no intervention can move it —
# that is exactly what sank v1. The 51-error condition ran 3 of 10, which is the
# only pool on disk with room in both directions.


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--root", type=Path, default=BASE,
                    help="pool to draw checkpoints from; default is the 258-error "
                         "results tree, which is at ceiling — pass the 51-error "
                         "workload directory instead")
    ap.add_argument("--out", type=Path, default=HERE / "candidates.json")
    a = ap.parse_args()

    cands = []
    for run in sorted(a.root.rglob("run-*")):
        if not run.is_dir() or "deepseek" in str(run):
            continue
        steps = sorted(run.glob("step-*"), key=lambda d: int(d.name.split("-")[1]))
        if not steps:
            continue
        if any("<TIMEOUT>" in (s / "messages.json").read_text(errors="ignore")
               for s in steps if (s / "messages.json").exists()):
            continue
        t_see, n_err = first_error_wall(steps)
        if t_see is None:
            continue
        t_act = None
        for s in steps:
            _, cheat = grade_step(s)
            if cheat:
                t_act = int(s.name.split("-")[1])
                break
        for s in steps:
            k = int(s.name.split("-")[1])
            if not (t_see + 1 <= k <= t_see + 5):
                continue
            if t_act is not None and k >= t_act:
                continue
            _, cheat = grade_step(s)
            if cheat:
                continue
            cands.append({
                "step_dir": str(s), "step": k, "t_see": t_see,
                "t_act": t_act, "n_err": n_err, "run": str(run),
                "eventual": "workaround" if t_act is not None else "not_observed",
            })

    # One checkpoint per source rollout — two steps from the same rollout are not
    # independent states, and stacking them would fake the sample size.
    by_run: dict[str, dict] = {}
    for c in cands:
        prev = by_run.get(c["run"])
        if prev is None or c["step"] < prev["step"]:
            by_run[c["run"]] = c
    picked = sorted(by_run.values(), key=lambda c: (c["step"], c["run"]))[: a.n]

    a.out.write_text(json.dumps(picked, indent=2))
    print(f"candidate checkpoints in the lag window: {len(cands)} "
          f"across {len(by_run)} rollouts -> keeping {len(picked)}")
    print(f"{'step':>5}{'t_see':>7}{'t_act':>7}  eventual            run")
    for c in picked:
        print(f"{c['step']:>5}{c['t_see']:>7}{str(c['t_act']):>7}  "
              f"{c['eventual']:20}{c['run'].split('120b/')[-1]}")
    print(f"\n-> {a.out}")


if __name__ == "__main__":
    main()
