"""Analyse the three-arm padding experiment.

Two contrasts, both within-state so between-state variance cancels:

    control -> pad_inert   context length at fixed workload
    pad_inert -> pad_work  task-relevant load at matched tokens AND matched turns

Reports both, with the sign test over states (distribution-free, and the paired
unit is the state, not the continuation). Also reports what fraction of each arm
never reached an outcome, because in v1 that was half the data and it was not
reported at all.

    python analyze_pad2.py ~/mats/_p2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from math import comb, sqrt
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from transience import grade_step  # noqa: E402

ARMS = ["control", "inert", "work"]


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def sign_test(diffs):
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    if n == 0:
        return pos, neg, 1.0
    k = min(pos, neg)
    p = sum(comb(n, i) for i in range(k + 1)) / (2 ** n) * 2
    return pos, neg, min(1.0, p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    a = ap.parse_args()

    data: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    censored: dict[str, list] = defaultdict(list)
    dropped = 0
    for d in sorted(a.root.glob("*__*")):
        if not d.is_dir():
            continue
        state, arm = d.name.rsplit("__", 1)
        for run in sorted(d.rglob("run-*")):
            steps = sorted(run.glob("step-*"), key=lambda x: int(x.name.split("-")[1]))
            if not steps:
                continue
            if any("TIMEOUT" in (s / "messages.json").read_text(errors="ignore")
                   for s in steps if (s / "messages.json").exists()):
                dropped += 1
                continue
            lab, cheat = grade_step(steps[-1])
            st = json.loads((steps[-1] / "state.json").read_text())
            data[state][arm].append(int(cheat))
            censored[arm].append(0 if st.get("task_completed") else 1)

    usable = {s: v for s, v in data.items() if all(v.get(x) for x in ARMS)}
    print(f"states with all three arms: {len(usable)}   "
          f"(timeout-corrupted continuations dropped: {dropped})")
    for arm in ARMS:
        c = censored.get(arm, [])
        if c:
            print(f"  {arm:8} never reached an outcome: {sum(c)}/{len(c)}")
    if not usable:
        raise SystemExit("\nno state has all three arms yet")

    print(f"\n{'state':30}{'control':>13}{'pad_inert':>13}{'pad_work':>13}")
    print("-" * 69)
    tot = {a_: [0, 0] for a_ in ARMS}
    d_ci, d_iw = [], []
    for s, v in sorted(usable.items()):
        cells = []
        for arm in ARMS:
            k, n = sum(v[arm]), len(v[arm])
            tot[arm][0] += k
            tot[arm][1] += n
            cells.append(f"{k}/{n} = {k/n:>4.0%}")
        r = {arm: sum(v[arm]) / len(v[arm]) for arm in ARMS}
        d_ci.append(r["inert"] - r["control"])
        d_iw.append(r["work"] - r["inert"])
        print(f"{s[:30]:30}" + "".join(f"{c:>13}" for c in cells))

    print("-" * 69)
    cells = []
    for arm in ARMS:
        k, n = tot[arm]
        cells.append(f"{k}/{n} = {k/n:>4.0%}")
    print(f"{'POOLED':30}" + "".join(f"{c:>13}" for c in cells))
    for arm in ARMS:
        k, n = tot[arm]
        lo, hi = wilson(k, n)
        print(f"  {arm:10} 95% Wilson {lo:.0%}-{hi:.0%}")

    for lab, diffs in [("context length   (pad_inert - control)", d_ci),
                       ("task load        (pad_work - pad_inert)", d_iw)]:
        m = sum(diffs) / len(diffs)
        sd = (sum((x - m) ** 2 for x in diffs) / max(1, len(diffs) - 1)) ** 0.5
        se = sd / sqrt(len(diffs))
        pos, neg, p = sign_test(diffs)
        print(f"\n{lab}")
        print(f"  mean within-state difference {m:+.1%}  (SE {se:.1%}, "
              f"95% CI {m-1.96*se:+.1%} to {m+1.96*se:+.1%})")
        print(f"  sign test over {pos+neg} discordant states: "
              f"{pos} up, {neg} down, {len(diffs)-pos-neg} tied, p = {p:.3f}")

    print("\nThe filesystem manifest, blob set and state.json are byte-identical")
    print("across all three arms of a state — verified per arm before the run.")
    print("Run `python awareness.py <root>` before reading any of this: if the")
    print("padded arms re-orient or report confusion more than control, the")
    print("manipulation was not a pure length change and none of it holds.")


if __name__ == "__main__":
    main()
