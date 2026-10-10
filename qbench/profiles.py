"""Model profiles: where a model is served and which settings ("presets") to test it with.

Nothing in the engine knows about specific models. Everything model-specific lives in
models/<name>.yaml, so adding a new open model means writing one small file.
"""

import copy
import glob
import os

import yaml

from .util import REPO

# Every request carries ALL of these. Servers have surprising defaults: llama.cpp
# silently applies top_k=40 and min_p=0.05 when a client sends only temperature/top_p.
SAMPLER_KEYS = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty",
                "presence_penalty", "frequency_penalty")
NEUTRAL = {"temperature": 0.7, "top_p": 1.0, "top_k": 0, "min_p": 0.0,
           "repeat_penalty": 1.0, "presence_penalty": 0.0, "frequency_penalty": 0.0}
# Keys a strict OpenAI-compatible server may reject; dropped when endpoint.strict_openai is true.
NON_OPENAI_KEYS = ("top_k", "min_p", "repeat_penalty", "cache_prompt")


def profile_path(name):
    if os.path.isfile(name):
        return name
    return os.path.join(REPO, "models", f"{name}.yaml")


def load_profile(name):
    path = profile_path(name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"no model profile '{name}' (looked for {path})")
    with open(path, encoding="utf-8") as f:
        prof = yaml.safe_load(f)
    prof.setdefault("name", os.path.splitext(os.path.basename(path))[0])
    prof["_path"] = path
    errs = validate_profile(prof)
    if errs:
        raise ValueError(f"{path}: " + "; ".join(errs))
    return prof


def list_profiles():
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(REPO, "models", "*.yaml"))
                  if not os.path.basename(p).startswith("_"))


def validate_profile(prof):
    errs = []
    for key in ("name", "family", "endpoint", "presets"):
        if key not in prof:
            errs.append(f"missing '{key}'")
    ep = prof.get("endpoint") or {}
    if not ep.get("base_url") or not ep.get("model"):
        errs.append("endpoint needs base_url and model")
    presets = prof.get("presets") or {}
    if not presets:
        errs.append("at least one preset is required")
    for pname, p in presets.items():
        if not isinstance(p, dict):
            errs.append(f"preset {pname} must be a mapping")
            continue
        if p.get("inherit") and p["inherit"] not in presets:
            errs.append(f"preset {pname} inherits unknown preset {p['inherit']}")
        if not p.get("inherit") and not p.get("source"):
            errs.append(f"preset {pname} needs a 'source' (where these settings come from)")
    return errs


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def preset(prof, name=None):
    """Resolve a preset (following `inherit`) into a complete, explicit settings dict:
    {name, source, sampling{all SAMPLER_KEYS}, max_tokens, extra_body, system_prompt}."""
    presets = prof["presets"]
    name = name or prof.get("default_preset") or next(iter(presets))
    if name not in presets:
        raise KeyError(f"model {prof['name']} has no preset '{name}' (has: {', '.join(presets)})")
    chain, cur = [], name
    while cur:
        if cur in chain:
            raise ValueError(f"preset inheritance loop at {cur}")
        chain.append(cur)
        cur = presets[cur].get("inherit")
    merged = {}
    for pname in reversed(chain):
        merged = _merge(merged, {k: v for k, v in presets[pname].items() if k != "inherit"})
    sampling = dict(NEUTRAL)
    sampling.update(merged.get("sampling") or {})
    unknown = set(sampling) - set(SAMPLER_KEYS)
    if unknown:
        raise ValueError(f"preset {name}: unknown sampler keys {sorted(unknown)}")
    return {
        "name": name,
        "source": merged.get("source") or presets[chain[-1]].get("source"),
        "sampling": sampling,
        "max_tokens": int(merged.get("max_tokens", prof.get("max_tokens", 4096))),
        "extra_body": merged.get("extra_body") or {},
        "system_prompt": merged.get("system_prompt"),  # optional extra system text, prepended
        "notes": merged.get("notes"),
    }
