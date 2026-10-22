"""The backend announces `ready` once at start. A page that loads or reloads afterwards (a dev reload, a crash and Reload) must be able to ask again, or the landing page waits forever
(Ryan, 2026-10-21: "just looks like it's loading and never actually loads"). `hello` answers with `ready` and the same settings summary."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO / "app" / "python_backend" / "backend.py"


def test_hello_answers_with_ready_again():
    p = subprocess.Popen([sys.executable, str(BACKEND)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(BACKEND.parent))
    try:
        p.stdin.write(b'{"type":"hello"}\n')
        p.stdin.flush()
        readies = []
        for _ in range(200):
            line = p.stdout.readline()
            if not line:
                break
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "ready":
                readies.append(e)
                if len(readies) == 2:
                    break
        assert len(readies) == 2, "the start-up ready and the answer to hello"
        assert readies[0]["version"] == readies[1]["version"] and "settings" in readies[1]
    finally:
        p.stdin.write(b'{"type":"shutdown"}\n')
        try:
            p.stdin.flush()
        except OSError:
            pass
        p.terminate()
