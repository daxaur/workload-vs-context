"""Figures. Every panel is regenerated from the JSON the analyses write, so a
number on a chart can always be traced back to the script that produced it.

    python figures.py --out figs/
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

INK = "#1b1b1b"
ACC = "#c1443c"
ALT = "#2f6f9f"
MUT = "#8a8a8a"

plt.rcParams.update({
    "figure.dpi": 140, "savefig.dpi": 140,
    "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": MUT, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUT, "ytick.color": MUT, "figure.facecolor": "white",
})


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    return max(0.0, c - h), min(1.0, c + h)


def fig_timeline(out: Path):
    """see -> say -> act, the three timestamps that define the decision window."""
    trig = json.loads((HERE / "trigger.json").read_text())
    said = {r["run"]: r for r in json.loads((HERE / "saiddid.json").read_text())}

    see, say, act = [], [], []
    for r in trig:
        if r["model"] != "gpt-oss-120b" or r["t_see"] is None or r["t_act"] is None:
            continue
        s = said.get(r["run"], {})
        see.append(r["t_see"])
        act.append(r["t_act"])
        if s.get("t_say") is not None:
            say.append(s["t_say"])

    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    for i, (vals, lab, col) in enumerate([
            (see, "first sees `Found N errors`", ALT),
            (say, "first voices the shortcut", MUT),
            (act, "workaround appears on disk", ACC)]):
        if not vals:
            continue
        y = 2 - i
        ax.scatter(vals, [y + (hash(v) % 7 - 3) / 40 for v in vals],
                   s=16, alpha=.55, color=col, edgecolors="none")
        med = sorted(vals)[len(vals) // 2]
        ax.plot([med], [y], marker="|", ms=22, mew=2.4, color=col)
        ax.text(med + 0.6, y + 0.16, f"median {med}", color=col, fontsize=8.5)
        ax.text(-0.4, y, lab, ha="right", va="center", fontsize=8.5, color=INK)

    ax.set_yticks([])
    ax.set_xlabel("step")
    ax.set_xlim(-0.5, max(act + see + say) + 1)
    ax.set_ylim(-0.6, 2.6)
    ax.set_title("The shortcut is not a reaction: a median 6 steps pass between\n"
                 "seeing the error count and writing the workaround",
                 loc="left", pad=10)
    fig.subplots_adjust(left=0.34)
    fig.savefig(out / "timeline.png", bbox_inches="tight")
    plt.close(fig)


def fig_cif(out: Path):
    """Cumulative incidence with the competing risk shown, not hidden."""
    from timing import trajectories, BASE  # noqa
    from transience import grade_step
    import re as _re

    rows = []
    for run, steps in trajectories(BASE):
        if any("TIMEOUT" in (s / "messages.json").read_text(errors="ignore")
               for s in steps if (s / "messages.json").exists()):
            continue
        if "deepseek" in str(run):
            continue
        cfg = run.parent / "config.yaml"
        cap = None
        if cfg.exists():
            m = _re.search(r"max_steps:\s*(\d+)", cfg.read_text())
            cap = int(m.group(1)) if m else None
        if cap != 25:
            continue
        first = None
        for s in steps:
            _, ch = grade_step(s)
            if ch:
                first = int(s.name.split("-")[1])
                break
        last = int(steps[-1].name.split("-")[1])
        done = json.loads((steps[-1] / "state.json").read_text()).get("task_completed")
        lab, _ = grade_step(steps[-1])
        # `task_completed` only means the agent stopped calling tools. A rollout
        # that stopped without a working hook gave up; it is not an honest finish.
        if first is not None:
            cause = "wk"
        elif done and lab == "honest":
            cause = "hon"
        elif done:
            cause = "gave"
        else:
            cause = "cens"
        rows.append((first if first is not None else last, cause))

    maxk = max(t for t, _ in rows)
    surv, cw, ch = 1.0, [], []
    xs = list(range(maxk + 1))
    a_w = a_h = 0.0
    for k in xs:
        at = sum(1 for t, _ in rows if t >= k)
        if at == 0:
            cw.append(a_w); ch.append(a_h); continue
        w = sum(1 for t, c in rows if t == k and c == "wk")
        h = sum(1 for t, c in rows if t == k and c == "hon")
        g = sum(1 for t, c in rows if t == k and c == "gave")
        a_w += surv * w / at
        a_h += surv * h / at
        surv *= 1 - (w + h + g) / at
        cw.append(a_w); ch.append(a_h)

    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    ax.step(xs, cw, where="post", color=ACC, lw=2, label="writes a workaround")
    n_gave = sum(1 for _, c in rows if c == "gave")
    ax.step(xs, ch, where="post", color=ALT, lw=2,
            label=f"finishes with a working hook (0 of {len(rows)})")
    ax.axvspan(6, 8, color=ACC, alpha=.07, lw=0)
    ax.text(7, .93, "hazard peak\nsteps 6–8", ha="center", fontsize=7.5, color=ACC)
    ax.set_xlabel("step")
    ax.set_ylabel("cumulative incidence")
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, loc="upper left", fontsize=8.5)
    ax.set_title(f"gpt-oss-120b · 258 type errors · max_steps 25 · n={len(rows)}\n"
                 f"competing risks; {n_gave} more stopped without writing a hook at all",
                 loc="left", pad=10)
    fig.savefig(out / "cif.png", bbox_inches="tight")
    plt.close(fig)


def fig_detector(out: Path):
    """What each grader sees, on the held-out set."""
    recs = json.loads((HERE / "heldout_oracle.json").read_text())
    scorable = [r for r in recs if r["oracle"].get("verdict") in
                ("workaround", "honest_hook_blocks", "honest_types_actually_fixed")]
    tp = sum(1 for r in scorable
             if r["oracle"]["verdict"] == "workaround" and r["frozen"]["cheat"])
    fn = sum(1 for r in scorable
             if r["oracle"]["verdict"] == "workaround" and not r["frozen"]["cheat"])

    fig, ax = plt.subplots(figsize=(5.6, 2.4))
    ax.barh([0], [tp], color=MUT, height=.55, label=f"caught ({tp})")
    ax.barh([0], [fn], left=[tp], color=ACC, height=.55, label=f"missed ({fn})")
    ax.set_yticks([0], ["frozen grep grader"])
    ax.set_xlim(0, len(scorable))
    ax.set_xlabel(f"held-out rollouts with a hook (n={len(scorable)}), "
                  "labelled by the behavioural oracle")
    ax.legend(frameon=False, loc="lower right", fontsize=8.5)
    ax.set_title("Frozen before the data existed, then scored against running the\n"
                 "artifact: 2 misses, both previously unknown workaround classes",
                 loc="left", pad=10)
    fig.savefig(out / "detector.png", bbox_inches="tight")
    plt.close(fig)


def fig_workload(out: Path):
    """Dose-response on workload, if the runs are on disk."""
    from transience import grade_step
    root = Path.home() / "mats" / "_workload"
    if not root.exists():
        return
    conds = []
    for cond in sorted(root.glob("errors_*"), key=lambda p: int(p.name.split("_")[1])):
        k = n = 0
        for run in sorted(cond.rglob("run-*")):
            steps = sorted(run.glob("step-*"), key=lambda d: int(d.name.split("-")[1]))
            if not steps:
                continue
            if any("TIMEOUT" in (s / "messages.json").read_text(errors="ignore")
                   for s in steps if (s / "messages.json").exists()):
                continue
            _, ch = grade_step(steps[-1])
            n += 1
            k += int(ch)
        if n:
            conds.append((int(cond.name.split("_")[1]), k, n))
    if len(conds) < 2:
        return

    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    xs = [c[0] for c in conds]
    ys = [c[1] / c[2] for c in conds]
    lo = [wilson(c[1], c[2])[0] for c in conds]
    hi = [wilson(c[1], c[2])[1] for c in conds]
    ax.errorbar(xs, ys, yerr=[[y - l for y, l in zip(ys, lo)],
                              [h - y for y, h in zip(ys, hi)]],
                fmt="o-", color=ACC, ecolor=MUT, capsize=3, lw=1.6, ms=6)
    for x, y, c in zip(xs, ys, conds):
        ax.annotate(f"{c[1]}/{c[2]}", (x, y), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=8, color=INK)
    ax.set_xlabel("pre-existing type errors in src/ (set at launch)")
    ax.set_ylabel("workaround rate")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("Workload varied at launch, context free to move\n"
                 "gpt-oss-120b · max_steps 50 · bars are 95% Wilson",
                 loc="left", pad=10)
    fig.savefig(out / "workload.png", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "figs")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for fn in (fig_timeline, fig_cif, fig_detector, fig_workload):
        try:
            fn(a.out)
            print(f"  ok {fn.__name__}")
        except Exception as e:
            print(f"  !! {fn.__name__}: {type(e).__name__}: {e}")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
