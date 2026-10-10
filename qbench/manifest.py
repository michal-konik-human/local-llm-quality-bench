"""The run manifest: everything an outsider needs to replicate a run.

Values are read back from the live server wherever possible. A config file says what
you meant to run; /props and /slots say what actually ran. When the server is not
llama.cpp, those fields are null with a reason, and the run still proceeds.
"""

import datetime as dt
import glob
import json
import os
import platform
import subprocess

from . import __version__
from .util import REPO, scrub_home, sha256_bytes, sha256_file, sha256_json


def now():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _run(cmd, timeout=10):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def bench_commit():
    sha = _run(["git", "-C", REPO, "rev-parse", "HEAD"])
    dirty = _run(["git", "-C", REPO, "status", "--porcelain", "--", "qbench", "cases", "envs", "models", "judges"])
    return {"commit": sha, "dirty": bool(dirty) if dirty is not None else None}


def host_info():
    info = {"os": platform.platform(), "python": platform.python_version(), "cpu": platform.processor() or None,
            "gpus": None}
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    names = []
    for p in sorted(glob.glob("/sys/class/drm/card*/device/product_name")):
        try:
            names.append(open(p).read().strip())
        except OSError:
            pass
    if not names:
        smi = _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
        names = [l.strip() for l in smi.splitlines()] if smi else []
    if not names:
        smi = _run(["rocm-smi", "--showproductname"])
        names = [l.split(":", 2)[-1].strip() for l in (smi or "").splitlines() if "Card Series" in l]
    info["gpus"] = names or None
    rocm = None
    if os.path.exists("/opt/rocm/.info/version"):
        rocm = open("/opt/rocm/.info/version").read().strip()
    info["rocm"] = rocm
    return info


def suite_info(cases, name="core"):
    items = [{"id": c.id, "version": c.get("version"), "sha256": c.sha256,
              "prompt_sha256": getattr(c, "prompt_sha256", None), "family": c.get("family"),
              "review": c.get("review"), "path": os.path.relpath(c.path, REPO) if c.path.startswith(REPO) else None}
             for c in cases]
    return {"name": name, "n_cases": len(items), "suite_sha256": sha256_json(sorted((i["id"], i["sha256"]) for i in items)),
            "cases": items}


def profile_info(prof):
    clean = {k: v for k, v in prof.items() if not k.startswith("_")}
    path = prof.get("_path")
    return {"profile": clean, "profile_sha256": sha256_file(path) if path and os.path.exists(path) else None}


def server_snapshot(client, model):
    """Best-effort capture of the serving stack. Every field may be None (with reasons)."""
    snap = {"base_url": scrub_home(client.base_url), "reasons": {}}
    props = client.get_json(f"/upstream/{model}/props") or client.get_json("/props")
    if props:
        tmpl = props.get("chat_template") or ""
        params = (props.get("default_generation_settings") or {}).get("params") or {}
        snap["llama_cpp"] = {
            "build_info": props.get("build_info"),
            "model_path": scrub_home(props.get("model_path")),
            "model_ftype": props.get("model_ftype"),
            "n_ctx": (props.get("default_generation_settings") or {}).get("n_ctx"),
            "total_slots": props.get("total_slots"),
            "chat_template_sha256": sha256_bytes(tmpl.encode()) if tmpl else None,
            "chat_template_caps": props.get("chat_template_caps"),
            "server_default_sampling": {k: params.get(k) for k in (
                "temperature", "top_k", "top_p", "min_p", "repeat_penalty", "presence_penalty",
                "frequency_penalty", "samplers")},
        }
        mp = props.get("model_path")
        if mp and os.path.exists(mp):
            shards = sorted(glob.glob(mp.replace("00001-of-", "*-of-"))) if "00001-of-" in mp else [mp]
            snap["llama_cpp"]["model_files"] = [{"name": os.path.basename(s), "bytes": os.path.getsize(s)} for s in shards]
    else:
        snap["llama_cpp"] = None
        snap["reasons"]["llama_cpp"] = "server has no /props (not llama.cpp, or endpoint disabled)"
    running = client.get_json("/running")
    if running and isinstance(running.get("running"), list):
        for r in running["running"]:
            if r.get("model") == model:
                snap["llama_swap"] = {"cmd": scrub_home(" ".join(r.get("cmd", "").split())), "ttl": r.get("ttl")}
                snap["llama_swap"]["cmd_sha256"] = sha256_bytes(snap["llama_swap"]["cmd"].encode())
    if "llama_swap" not in snap:
        snap["llama_swap"] = None
        snap["reasons"]["llama_swap"] = "no llama-swap /running entry for this model"
    return snap


def effective_sampling(client, model, seed):
    """Read back the sampler params the server actually used for our request (llama.cpp /slots)."""
    slots = client.get_json(f"/upstream/{model}/slots") or client.get_json("/slots")
    if not isinstance(slots, list):
        return None
    for s in slots:
        p = s.get("params") or {}
        if p.get("seed") == seed:
            return {k: p.get(k) for k in ("temperature", "top_k", "top_p", "min_p", "repeat_penalty",
                                          "presence_penalty", "frequency_penalty", "seed", "n_predict",
                                          "samplers", "chat_format", "reasoning_format")}
    return None


def compare_sampling(requested, effective):
    """List sampler keys whose effective value differs from what we sent."""
    if not effective:
        return None
    diffs = []
    for k, v in requested.items():
        e = effective.get(k)
        if e is None:
            continue
        if isinstance(v, (int, float)) and isinstance(e, (int, float)):
            if abs(float(v) - float(e)) > 1e-4:
                diffs.append({"param": k, "sent": v, "server_used": e})
    return diffs


def build_manifest(run_id, prof, preset, cases, k, langs, argv, suite="core"):
    return {
        "qbench_version": __version__,
        "run_id": run_id,
        "started_at": now(),
        "finished_at": None,
        "command": scrub_home(" ".join(argv)),
        "bench": bench_commit(),
        "host": host_info(),
        "suite": suite_info(cases, suite),
        "model": profile_info(prof),
        "preset": preset,
        "k": k,
        "langs": langs,
        "request_policy": {
            "sequential": True,
            "cache_prompt": False if not prof["endpoint"].get("strict_openai") else "not sent (strict_openai)",
            "seed": "crc32(run_id|case|lang|sample) + turn",
            "all_samplers_explicit": not prof["endpoint"].get("strict_openai"),
            "strict_openai": bool(prof["endpoint"].get("strict_openai")),
        },
        "server": None,
        "effective_sampling": None,
        "sampling_mismatches": None,
        "counts": None,
    }


def save_manifest(run_dir, manifest):
    from .util import write_json
    write_json(os.path.join(run_dir, "manifest.json"), scrub_home(manifest))


def load_manifest(run_dir):
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)
