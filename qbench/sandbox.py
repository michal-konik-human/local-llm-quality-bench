"""Run model-written Python against hidden tests, isolated from the host as far as stdlib allows.

Protection layers: a fresh temp directory as cwd, `python -I` (no user site, no env vars),
an empty environment, CPU/memory/file-size rlimits, a wall-clock timeout, and no network
when `unshare -rn` is available (Linux user namespaces). This is a guard against
accidents and sloppy code, not a security boundary against a hostile model. Do not run
the coding family on a machine holding secrets you would not hand to the model.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HARNESS = r'''
import json, sys
src = open("solution.py", encoding="utf-8").read()
tests = json.load(open("tests.json", encoding="utf-8"))
ns = {"__name__": "solution"}
out = {"loaded": False, "results": []}
try:
    exec(compile(src, "solution.py", "exec"), ns)
    out["loaded"] = True
except BaseException as e:
    out["error"] = f"{type(e).__name__}: {e}"
if out["loaded"]:
    for t in tests:
        try:
            exec(compile(t, "test", "exec"), dict(ns))
            out["results"].append([t, True, ""])
        except BaseException as e:
            out["results"].append([t, False, f"{type(e).__name__}: {e}"[:300]])
sys.stdout.write("\n@@RESULT@@" + json.dumps(out))
'''

_UNSHARE = None


def _unshare_prefix():
    global _UNSHARE
    if _UNSHARE is None:
        _UNSHARE = []
        if sys.platform.startswith("linux") and shutil.which("unshare"):
            try:
                r = subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5)
                if r.returncode == 0:
                    _UNSHARE = ["unshare", "-rn"]
            except (OSError, subprocess.SubprocessError):
                pass
    return _UNSHARE


def _limits(cpu_s, mem_mb):
    def apply():
        import resource
        # each limit is best-effort: macOS, for one, rejects RLIMIT_AS
        for lim, val in ((resource.RLIMIT_CPU, cpu_s), (resource.RLIMIT_AS, mem_mb << 20),
                         (resource.RLIMIT_FSIZE, 10 << 20)):
            try:
                resource.setrlimit(lim, (val, val))
            except (ValueError, OSError):
                pass
        os.setsid()
    return apply


def run_python(code, tests, timeout=15, mem_mb=1024):
    """-> {loaded, passed, total, results[[test, ok, err]], error, network_isolated}"""
    with tempfile.TemporaryDirectory(prefix="qbench-sbx-") as d:
        with open(os.path.join(d, "solution.py"), "w", encoding="utf-8") as f:
            f.write(code)
        with open(os.path.join(d, "tests.json"), "w", encoding="utf-8") as f:
            json.dump(list(tests), f)
        with open(os.path.join(d, "harness.py"), "w", encoding="utf-8") as f:
            f.write(HARNESS)
        prefix = _unshare_prefix()
        cmd = prefix + [sys.executable, "-I", "harness.py"]
        preexec = _limits(timeout, mem_mb) if os.name == "posix" else None
        try:
            r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout + 5,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, preexec_fn=preexec)
            raw = r.stdout
        except subprocess.TimeoutExpired:
            return {"loaded": False, "passed": 0, "total": len(tests), "results": [],
                    "error": f"timeout after {timeout}s", "network_isolated": bool(prefix)}
        if "@@RESULT@@" not in raw:
            return {"loaded": False, "passed": 0, "total": len(tests), "results": [],
                    "error": (r.stderr or "no output")[-500:], "network_isolated": bool(prefix)}
        out = json.loads(raw.rsplit("@@RESULT@@", 1)[1])
        out["passed"] = sum(1 for _, ok, _ in out["results"] if ok)
        out["total"] = len(tests)
        out["network_isolated"] = bool(prefix)
        return out


def extract_code(text):
    """The last ```python fenced block, else the last fenced block, else the whole text."""
    import re
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text or "", re.S)
    if not blocks:
        blocks = re.findall(r"```[\w-]*\s*\n(.*?)```", text or "", re.S)
    return blocks[-1] if blocks else (text or "")
