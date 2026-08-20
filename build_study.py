#!/usr/bin/env python3
"""Inject the two blinded label samples into study.html.

study.html is committed with the data already inside so it works offline with no
build step. Re-run this only if saiddid_sample.md or monitorability_sample.md
change:

    python build_study.py

Mechanical extraction. Nothing is rewritten or summarised; the quotes in
study.html are byte-identical to the ones in the sample files.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = re.compile(r"^const SAMPLES = .*$", re.M)


def split_items(text):
    """Yield (n, rollout, body) for each '## N. `rollout`' block."""
    parts = re.split(r"^## (\d+)\.\s*`([^`]+)`\s*$", text, flags=re.M)
    for i in range(1, len(parts), 3):
        yield int(parts[i]), parts[i + 1], parts[i + 2]


def parse_saiddid(path):
    """saiddid_sample.md -> one record per rollout.

    `fired` is False where the classifier produced no match. Those rollouts have
    no quote in the file, so they cannot be labelled from it: they are what
    recall would need, and recall is therefore not computable here.
    """
    out = []
    for n, rollout, body in split_items(path.read_text()):
        said = re.search(r"classifier said:\s*\*\*([^*]+)\*\*\s*\(step (\w+)\)", body)
        disk = re.search(r"workaround on disk at step:\s*(\S+)", body)
        quote = re.search(r"- quote:\s*>\s*(.*?)(?=\n\nyour label|\Z)", body, flags=re.S)
        q = quote.group(1).strip() if quote else ""
        step = said.group(2) if said else "None"
        disk_step = disk.group(1).strip() if disk else "None"
        out.append({
            "n": n,
            "rollout": rollout,
            "said": said.group(1).strip() if said else None,
            "saidStep": int(step) if step.isdigit() else None,
            "diskStep": int(disk_step) if disk_step.isdigit() else None,
            "quote": q,
            "fired": q not in ("", "_(no match)_"),
        })
    return out


def parse_monitor(path):
    """monitorability_sample.md -> one record per rollout, tiers preserved."""
    out = []
    for n, rollout, body in split_items(path.read_text()):
        disk = re.search(r"workaround on disk:\s*\*\*(\w+)\*\*(?:\s*\(step (\d+)\))?", body)
        tiers = []
        for m in re.finditer(
            r"- \*\*(\w+)\*\* \(step (\d+)\):\s*>\s*(.*?)(?=\n- \*\*|\n\nyour label|\Z)",
            body, flags=re.S,
        ):
            tiers.append({
                "tier": m.group(1),
                "step": int(m.group(2)),
                "quote": m.group(3).strip(),
            })
        out.append({
            "n": n,
            "rollout": rollout,
            "disk": disk.group(1) == "True" if disk else None,
            "diskStep": int(disk.group(2)) if disk and disk.group(2) else None,
            "tiers": tiers,
            "fired": bool(tiers),
        })
    return out


def main():
    said = parse_saiddid(HERE / "saiddid_sample.md")
    mon = parse_monitor(HERE / "monitorability_sample.md")

    data = {"saiddid": said, "monitorability": mon}
    blob = json.dumps(data, separators=(",", ":"))
    # the payload lands inside a <script> block; `<` only ever occurs inside JSON
    # string literals, where < is an equivalent escape.
    payload = "const SAMPLES = " + blob.replace("<", "\\u003c") + ";"

    target = HERE / "study.html"
    html = target.read_text()
    if not MARKER.search(html):
        sys.exit("no `const SAMPLES = ...` line found in study.html")
    target.write_text(MARKER.sub(lambda _: payload, html, count=1))

    sf = sum(1 for x in said if x["fired"])
    mf = sum(1 for x in mon if x["fired"])
    print(f"saiddid        {len(said)} rollouts, {sf} labellable, {len(said) - sf} no-match")
    print(f"monitorability {len(mon)} rollouts, {mf} labellable, {len(mon) - mf} no-match")
    print(f"study.html     {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
