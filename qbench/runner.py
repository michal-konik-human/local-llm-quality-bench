"""Generate samples: one record per (case, language, sample index).

Records are append-only (samples.jsonl). A run that is interrupted resumes where it
stopped: samples with an outcome (an answer, or running out of turns) are skipped; samples
that failed for transport/server reasons are retried.
Each record carries the exact request body, so any single sample can be replayed.
"""

import copy
import json
import os
import time

from .adapters import normalize
from .cases import render, resolve
from .client import Client, HTTPError
from .envs import make_env
from .manifest import (build_manifest, compare_sampling, effective_sampling, now,
                       save_manifest, server_snapshot)
from .profiles import NON_OPENAI_KEYS, preset as resolve_preset
from .util import append_jsonl, error_class, read_jsonl, scrub_home, seed_for

DEFAULT_MAX_TURNS = {"agentic": 10, "web_shopping": 12, "web_research": 20}


def build_messages(case, lang, preset):
    spec = case.lang(lang)
    system = "\n\n".join(s for s in (preset.get("system_prompt"), spec.get("system")) if s)
    msgs = [{"role": "system", "content": system}] if system else []
    for m in copy.deepcopy(spec["messages"]):
        m["content"] = render(case, m["content"], lang)
        msgs.append(m)
    return msgs


def case_tools(case, env):
    tools = copy.deepcopy(case.get("tools") or [])
    if env is not None:
        tools += env.tools()
    return tools


def max_tokens_for(case, preset):
    """The preset's budget, raised to the case's `max_tokens` when the case needs more (long documents,
    long agent runs). Never lowered: a case cannot make a model look worse by starving it."""
    return max(int(preset["max_tokens"]), int(case.get("max_tokens") or 0))


def request_body(prof, preset, messages, tools, seed, max_tokens=None):
    body = {"model": prof["endpoint"]["model"], "messages": messages, "stream": False,
            "max_tokens": max_tokens or preset["max_tokens"], "seed": seed, "cache_prompt": False}
    body.update(preset["sampling"])
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    for k, v in (preset.get("extra_body") or {}).items():
        body[k] = copy.deepcopy(v)
    if prof["endpoint"].get("strict_openai"):
        for k in NON_OPENAI_KEYS:
            body.pop(k, None)
    return body


def _assistant_message(norm, turn, echo_reasoning):
    msg = {"role": "assistant", "content": norm["content"] or ""}
    if norm["tool_calls"]:
        msg["tool_calls"] = []
        for j, tc in enumerate(norm["tool_calls"]):
            tc_id = tc.get("id") or f"call_{turn}_{j}"
            tc["id"] = tc_id
            args = tc["arguments"]
            msg["tool_calls"].append({"id": tc_id, "type": "function", "function": {
                "name": tc["name"], "arguments": args.get("__raw__") if "__raw__" in args
                else json.dumps(args, ensure_ascii=False)}})
    if echo_reasoning and norm["reasoning"]:
        msg["reasoning_content"] = norm["reasoning"]
    return msg


def run_sample(client, prof, preset, case, lang, sample_idx, run_id):
    """Run one sample through the (possibly multi-turn) tool loop. Never raises on model/server errors."""
    seed = seed_for(run_id, case.id, lang, sample_idx)
    env = make_env(resolve(case.get("env"), lang), lang) if case.get("env") else None
    tools = case_tools(case, env)
    messages = build_messages(case, lang, preset)
    max_turns = int(case.get("max_turns") or (DEFAULT_MAX_TURNS.get(case["family"], 1) if env else 1))
    echo = prof["endpoint"].get("echo_reasoning", True)
    rec = {"run_id": run_id, "case": case.id, "case_sha256": case.sha256, "case_version": case.get("version"),
           "family": case["family"], "item_type": case["item_type"], "review": case["review"],
           "lang": lang, "sample": sample_idx, "seed": seed, "model": prof["name"], "preset": preset["name"],
           "started_at": now(), "turns": [], "request": None, "final": None, "all_tool_calls": [],
           "env_state": None, "env_calls": None, "usage": {"prompt_tokens": 0, "completion_tokens": 0},
           "latency_s": 0.0, "error": None}
    first_body = None
    for turn in range(max_turns):
        body = request_body(prof, preset, messages, tools, seed + turn, max_tokens_for(case, preset))
        if first_body is None:
            first_body = copy.deepcopy(body)
        t0 = time.time()
        try:
            resp = client.chat(body)
        except (HTTPError, OSError, TimeoutError, ValueError) as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            break
        dt_s = time.time() - t0
        norm = normalize(resp, tools_offered=bool(tools))
        usage = resp.get("usage") or {}
        timings = resp.get("timings") or {}
        rec["usage"]["prompt_tokens"] += usage.get("prompt_tokens") or 0
        rec["usage"]["completion_tokens"] += usage.get("completion_tokens") or 0
        rec["latency_s"] += dt_s
        rec["turns"].append({"turn": turn, "seed": seed + turn, "latency_s": round(dt_s, 3), "usage": usage,
                             "timings": {k: timings.get(k) for k in ("prompt_n", "prompt_per_second",
                                                                     "predicted_n", "predicted_per_second")},
                             "finish_reason": norm["finish_reason"], "tool_parse": norm["tool_parse"],
                             "content": norm["content"], "reasoning": norm["reasoning"],
                             "tool_calls": norm["tool_calls"]})
        rec["final"] = {k: norm[k] for k in ("content", "reasoning", "tool_calls", "tool_parse", "finish_reason")}
        rec["all_tool_calls"] += norm["tool_calls"]
        messages.append(_assistant_message(norm, turn, echo))
        if not (env and norm["tool_calls"]):
            break
        for tc in norm["tool_calls"]:
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": env.call(tc["name"], tc["arguments"])})
    else:
        if env and rec["final"] and rec["final"]["tool_calls"]:
            rec["error"] = f"max_turns ({max_turns}) reached while the model was still calling tools"
    rec["request"] = first_body
    rec["transcript"] = messages
    rec["latency_s"] = round(rec["latency_s"], 3)
    if env:
        rec["env_state"] = env.state()
        rec["env_calls"] = env.calls
    rec["finished_at"] = now()
    return scrub_home(rec)


def run_suite(prof, preset_name, cases, k, run_dir, langs=None, client=None, argv=(), log=print, suite="core"):
    os.makedirs(run_dir, exist_ok=True)
    run_id = os.path.basename(os.path.normpath(run_dir))
    preset = resolve_preset(prof, preset_name)
    client = client or Client.from_profile(prof)
    langs = list(langs or ("pl", "en"))
    samples_path = os.path.join(run_dir, "samples.jsonl")
    manifest_path = os.path.join(run_dir, "manifest.json")
    if os.path.exists(manifest_path):
        from .manifest import load_manifest
        manifest = load_manifest(run_dir)
        if manifest["model"]["profile"]["name"] != prof["name"] or manifest["preset"]["name"] != preset["name"]:
            raise SystemExit(f"{run_dir} belongs to {manifest['model']['profile']['name']}/"
                             f"{manifest['preset']['name']}; use a new run directory")
        manifest["k"] = max(manifest.get("k") or 0, k)
    else:
        manifest = build_manifest(run_id, prof, preset, cases, k, langs, list(argv), suite)
    save_manifest(run_dir, manifest)
    # done = has an outcome (a normal answer, or the model ran out of turns: that is its behaviour, not a
    # glitch, so it must not be re-rolled on resume). Transport/server errors are retried.
    done = {(r["case"], r["lang"], r["sample"]) for r in read_jsonl(samples_path) if error_class(r) != "transport"}
    todo = [(c, lang, i) for c in cases for lang in c.langs() if lang in langs for i in range(k)
            if (c.id, lang, i) not in done]
    log(f"[run] {prof['name']} / preset {preset['name']}: {len(todo)} samples to generate "
        f"({len(done)} already done) -> {run_dir}")
    t_start, n_err = time.time(), 0
    for n, (case, lang, i) in enumerate(todo, 1):
        rec = run_sample(client, prof, preset, case, lang, i, run_id)
        append_jsonl(samples_path, rec)
        if rec["error"]:
            n_err += 1
        if manifest.get("server") is None and not rec["error"]:
            manifest["server"] = server_snapshot(client, prof["endpoint"]["model"])
            eff = effective_sampling(client, prof["endpoint"]["model"], rec["turns"][-1]["seed"])
            manifest["effective_sampling"] = eff
            manifest["sampling_mismatches"] = compare_sampling(preset["sampling"], eff)
            if manifest["sampling_mismatches"]:
                log(f"[warn] server used different samplers than requested: {manifest['sampling_mismatches']}")
            save_manifest(run_dir, manifest)
        took = rec["latency_s"]
        status = "ERR " + rec["error"][:80] if rec["error"] else f"{took:6.1f}s"
        log(f"  [{n}/{len(todo)}] {case.id} {lang} #{i} {status}")
    all_recs = read_jsonl(samples_path)
    manifest["finished_at"] = now()
    manifest["counts"] = {"records": len(all_recs), "errors": sum(1 for r in all_recs if r.get("error")),
                          "wall_s": round(time.time() - t_start, 1)}
    save_manifest(run_dir, manifest)
    log(f"[run] done: {len(todo)} generated, {n_err} errors, {manifest['counts']['wall_s']} s")
    return manifest
