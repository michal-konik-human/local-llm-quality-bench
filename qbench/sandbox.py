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
marker = sys.argv[1]
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
sys.stdout.write("\n" + marker + json.dumps(out) + "\n")
sys.stdout.flush()
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
    """-> {loaded, passed, total, results[[test, ok, err]], error, network_isolated}

    Results are read after a random per-run marker, only the tests we passed in are counted
    (a model cannot add its own "passing" results), and `passed` is clipped to the total."""
    import secrets
    import signal
    tests = list(tests)
    marker = "@@QBENCH-" + secrets.token_hex(16) + "@@"
    with tempfile.TemporaryDirectory(prefix="qbench-sbx-") as d:
        with open(os.path.join(d, "solution.py"), "w", encoding="utf-8") as f:
            f.write(code)
        with open(os.path.join(d, "tests.json"), "w", encoding="utf-8") as f:
            json.dump(tests, f)
        with open(os.path.join(d, "harness.py"), "w", encoding="utf-8") as f:
            f.write(HARNESS)
        prefix = _unshare_prefix()
        cmd = prefix + [sys.executable, "-I", "harness.py", marker]
        preexec = _limits(timeout, mem_mb) if os.name == "posix" else None
        base = {"loaded": False, "passed": 0, "total": len(tests), "results": [], "network_isolated": bool(prefix)}
        proc = subprocess.Popen(cmd, cwd=d, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                                preexec_fn=preexec)
        try:
            raw, err = proc.communicate(timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # the child called setsid(): kill its whole group
            except (OSError, AttributeError):
                proc.kill()
            proc.communicate()
            return {**base, "error": f"timeout after {timeout}s"}
        if marker not in raw:
            return {**base, "error": (err or "no output")[-500:]}
        try:
            out = json.loads(raw.split(marker, 1)[1].splitlines()[0])
        except (ValueError, IndexError):
            return {**base, "error": "unparseable harness output"}
        got = {t: (ok, e) for t, ok, e in out.get("results", []) if t in tests}
        results = [[t, bool(got.get(t, (False, ""))[0]), got.get(t, (False, "not run"))[1]] for t in tests]
        return {**base, "loaded": bool(out.get("loaded")), "error": out.get("error"), "results": results,
                "passed": min(len(tests), sum(1 for _, ok, _ in results if ok))}


def extract_code(text, entry=None):
    """The last ```python block that defines `entry` (so a trailing usage example is skipped),
    else the last python block, else the last fenced block, else the whole text."""
    import re
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text or "", re.S)
    if not blocks:
        blocks = re.findall(r"```[\w-]*\s*\n(.*?)```", text or "", re.S)
    if entry:
        defining = [b for b in blocks if re.search(rf"^\s*def\s+{re.escape(entry)}\s*\(", b, re.M)]
        if defining:
            return defining[-1]
    return blocks[-1] if blocks else (text or "")
