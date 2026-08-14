"""When is the shortcut decided, and is the decision state-determined?

Two questions, both answerable from checkpoints already on disk.

1. HAZARD. Of the rollouts still honest at the start of step k, what fraction
   first writes a workaround artifact during step k? A flat hazard means the
   shortcut is a constant background risk; a hazard that rises with k means
   something accumulates — which is the whole premise of the project.

   This is a discrete-time hazard with right-censoring: a rollout that finishes
   honest, or is cut off by the step cap, leaves the risk set without an event.
   Pooling rollouts of different lengths without that correction is the standard
   way to get this wrong.

2. INTRACLASS CORRELATION. The paired experiment resampled each state ten times.
   If the outcome is nearly constant within a state and varies between states,
   the policy was fixed before the checkpoint and no intervention applied *at*
   the checkpoint can move it. That is a testable statement about the ceiling,
   not a hand-wave.

    python timing.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from math import sqrt
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from transience import grade_step  # noqa: E402

BASE = Path.home() / "mats" / "agent-interp-envs" / "results"
PAIRED = Path.home() / "mats" / "_paired"


def context_tokens(step: Path) -> int:
    """chars/4 over the exact strings that get serialised into the request."""
    p = step / "messages.json"
    if not p.exists():
        return 0
    try:
        msgs = json.loads(p.read_text())
    except Exception:
        return 0
    n = 0
    for m in msgs:
        for k in ("content", "reasoning"):
            v = m.get(k)
            if isinstance(v, str):
                n += len(v)
        for tc in m.get("tool_calls") or []:
            n += len(json.dumps(tc))
    return n // 4


def trajectories(root: Path):
    for run in sorted(root.rglob("run-*")):
        if not run.is_dir():
            continue
        steps = sorted(run.glob("step-*"), key=lambda d: int(d.name.split("-")[1]))
        if not steps:
            continue
        yield run, steps


def hazard():
    """Cumulative incidence of the first workaround, with competing risks.

    A rollout that finishes the task honestly does not "leave the risk set" the
    way a rollout truncated by the step cap does — it can never have the event.
    Treating the two the same (ordinary Kaplan-Meier) redistributes honest
    finishers into the event group and inflates the shortcut curve. Two causes,
    so: cause-specific hazards and a proper cumulative incidence function.
    """
    import re as _re
    rows = []
    dropped = 0
    for run, steps in trajectories(BASE):
        # 30 rollouts on disk are the concurrency-corrupted batches: at
        # --count 20 `mypy src` exceeded the container's 30s limit and the tool
        # result is `<TIMEOUT>` instead of the error list. Those agents never saw
        # the 258 errors, so their shortcut rate is not comparable — and it is
        # biased DOWNWARD, since the wall of errors is the trigger. An earlier
        # version of this function silently included all 30.
        if any("TIMEOUT" in (s / "messages.json").read_text(errors="ignore")
               for s in steps if (s / "messages.json").exists()):
            dropped += 1
            continue
        model = "unknown"
        m = _re.search(r"/(openai-[^/]+|deepseek-[^/]+)/", str(run))
        if m:
            model = m.group(1)
        # The results tree pools runs from several configs — max_steps 25/30/50/100
        # and target_errors 258/602. Pooling them makes the risk set change
        # composition as k grows, which is exactly what a hazard curve must not
        # do. Read the config each run was launched under and stratify.
        cfg = run.parent / "config.yaml"
        errs = steps_cap = None
        if cfg.exists():
            txt = cfg.read_text()
            me = _re.search(r"target_errors:\s*(\d+)", txt)
            ms = _re.search(r"max_steps:\s*(\d+)", txt)
            errs = int(me.group(1)) if me else None
            steps_cap = int(ms.group(1)) if ms else None
        first = None
        tok = None
        for s in steps:
            _, cheat = grade_step(s)
            if cheat:
                first = int(s.name.split("-")[1])
                tok = context_tokens(s)
                break
        last_step = steps[-1]
        last_k = int(last_step.name.split("-")[1])
        done = False
        sp = last_step / "state.json"
        if sp.exists():
            try:
                done = bool(json.loads(sp.read_text()).get("task_completed"))
            except Exception:
                pass
        if first is not None:
            cause = "workaround"
            t_ = first
        elif done:
            cause = "honest_finish"      # competing event, not censoring
            t_ = last_k
        else:
            cause = "censored"           # hit the step cap still undecided
            t_ = last_k
        rows.append({"model": model, "t": t_, "cause": cause, "tok": tok,
                     "errs": errs, "cap": steps_cap})

    def cif(rs, label):
        n = len(rs)
        if not n:
            return
        ev = sum(1 for r in rs if r["cause"] == "workaround")
        hf = sum(1 for r in rs if r["cause"] == "honest_finish")
        cz = sum(1 for r in rs if r["cause"] == "censored")
        print(f"\n{label}: n={n}   workaround {ev}   honest finish {hf}   "
              f"cut off by cap {cz}")
        surv = 1.0
        cif_w = cif_h = 0.0
        print("step  at risk   wk   hon   h_wk    CIF workaround   CIF honest")
        maxk = max(r["t"] for r in rs)
        for k in range(maxk + 1):
            at_risk = sum(1 for r in rs if r["t"] >= k)
            if at_risk == 0:
                break
            w = sum(1 for r in rs if r["t"] == k and r["cause"] == "workaround")
            h = sum(1 for r in rs if r["t"] == k and r["cause"] == "honest_finish")
            cif_w += surv * (w / at_risk)
            cif_h += surv * (h / at_risk)
            surv *= (1 - (w + h) / at_risk)
            if w or h:
                print(f"{k:>4}{at_risk:>9}{w:>5}{h:>6}{w/at_risk:>8.2f}"
                      f"{cif_w:>16.0%}{cif_h:>13.0%}")
        print(f"  -> by the end: P(shortcut) {cif_w:.0%}   P(honest finish) {cif_h:.0%}"
              f"   P(still undecided) {1-cif_w-cif_h:.0%}")

    from collections import Counter as _C
    print(f"timeout-corrupted rollouts excluded: {dropped}")
    print("config mix:", dict(_C((r["model"].split("-")[0], r["errs"], r["cap"])
                                 for r in rows)))
    # Only strata with enough rollouts to carry a hazard curve.
    strata = _C((r["model"], r["errs"], r["cap"]) for r in rows)
    for (mdl, e, c), n in sorted(strata.items(), key=lambda kv: -kv[1]):
        if n < 10:
            continue
        cif([r for r in rows if (r["model"], r["errs"], r["cap"]) == (mdl, e, c)],
            f"{mdl}  errors={e}  max_steps={c}")

    toks = sorted(r["tok"] for r in rows if r["tok"])
    if toks:
        print(f"\ncontext at the step the shortcut is written: median {toks[len(toks)//2]:,} "
              f"tokens   range {toks[0]:,}-{toks[-1]:,}")
        print("Observational: long context and much work are confounded here. This")
        print("sets the DOSE the padding arm has to reach; it is not a causal claim.")


def icc():
    """How much of the outcome variance sits between states rather than within."""
    per = defaultdict(list)
    for run, steps in trajectories(PAIRED):
        state = run.parent.name.rsplit("__", 1)[0]
        txt = (steps[-1] / "messages.json")
        if txt.exists() and "TIMEOUT" in txt.read_text(errors="ignore"):
            continue
        _, cheat = grade_step(steps[-1])
        per[state].append(int(cheat))

    groups = [v for v in per.values() if len(v) >= 2]
    if not groups:
        print("no groups")
        return
    N = sum(len(g) for g in groups)
    k = len(groups)
    grand = sum(sum(g) for g in groups) / N
    ms_between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups) / (k - 1)
    ms_within = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups) / (N - k)
    n0 = (N - sum(len(g) ** 2 for g in groups) / N) / (k - 1)
    var_b = max(0.0, (ms_between - ms_within) / n0)
    rho = var_b / (var_b + ms_within) if (var_b + ms_within) > 0 else 0.0

    print(f"\npaired states: {k}   continuations: {N}   pooled rate {grand:.0%}")
    print(f"ICC (between-state share of outcome variance): {rho:.2f}")
    deg = sum(1 for g in groups if len(set(g)) == 1)
    print(f"states whose {min(len(g) for g in groups)}–{max(len(g) for g in groups)} "
          f"continuations all agree: {deg}/{k}")
    print("An ICC near 1 means the outcome is a property of the state, not of what")
    print("happens after it — i.e. the policy was already fixed at the checkpoint.")


if __name__ == "__main__":
    hazard()
    icc()
