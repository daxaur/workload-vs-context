"""Rebuild a checkpoint and run a command list for real. Runs INSIDE the image.

Prints one JSON line: [[command, "Exit code: N\\nOutput:\\n<real output>"], ...]

Shares the rebuild logic with probe_in_container.py deliberately — both need the
same "pristine plus everything the agent wrote" workspace, and the point of both
is that the bytes are real rather than asserted.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

SNAP = Path("/snap")
W = Path("/agent")
TASK = Path("/opt/task")


def rebuild() -> None:
    if W.exists():
        shutil.rmtree(W, ignore_errors=True)
    W.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TASK / "src_258", W / "src")
    shutil.copy(TASK / "pyproject.toml", W / "pyproject.toml")
    man = json.loads((SNAP / "manifest.json").read_text())
    for e in man.get("entries", []):
        p = e.get("path", "")
        if not p.startswith("/agent/"):
            continue
        dst = Path(p)
        if e.get("action") == "mkdir":
            dst.mkdir(parents=True, exist_ok=True)
        elif e.get("action") == "write":
            blob = SNAP / "blobs" / str(e.get("blob"))
            if not blob.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(blob, dst)
            os.chmod(dst, e.get("mode", 0o644))
        elif e.get("action") in ("delete", "unlink", "remove"):
            if dst.is_file():
                dst.unlink()
    if not (W / ".git").exists():
        subprocess.run("git init -q && git add -A && "
                       "git -c user.email=d@e.com -c user.name=d "
                       "commit --no-verify -q -m base",
                       shell=True, cwd=str(W), capture_output=True)


def main() -> None:
    rebuild()
    cmds = json.loads((SNAP / "cmds.json").read_text())
    out = []
    for c in cmds:
        try:
            r = subprocess.run(c, shell=True, cwd=str(W), capture_output=True,
                               text=True, timeout=120)
            body = (r.stdout or "") + (r.stderr or "")
            out.append([c, f"Exit code: {r.returncode}\nOutput:\n{body.rstrip()}"])
        except subprocess.TimeoutExpired:
            out.append([c, "Exit code: 124\nOutput:\n<TIMEOUT>"])
    print(json.dumps(out))


if __name__ == "__main__":
    main()
