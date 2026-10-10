"""Grade a run: deterministic scorers for every check, the LLM judge for rubric checks.

Grading never touches samples.jsonl (generate once, grade many). Judge verdicts are cached
in judgments.jsonl keyed by (sample, check, judge, hash of the full judge request + rubric
weights), so re-grading after a scorer fix is instant and only new or changed rubric checks
reach the judge.
"""

import os

from .cases import load_case
from .judge import PROMPT_SHA, grade as judge_grade, pick_judge
from .manifest import load_manifest, now
from .scorers import score_check
from .util import REPO, append_jsonl, error_class, read_jsonl, write_json


def sample_key(rec):
    return f"{rec['case']}|{rec['lang']}|{rec['sample']}"


def is_outcome(rec):
    """A record the MODEL is responsible for (see util.error_class): a normal answer, running out of
    turns, or output the server could not parse. Transport/server errors are not outcomes."""
    return error_class(rec) != "transport"


def latest_samples(run_dir):
    """One record per (case, lang, sample): the FIRST outcome record (a resume that regenerates a
    sample must not re-roll a bad answer), else the latest transport-error record."""
    best = {}
    for rec in read_jsonl(os.path.join(run_dir, "samples.jsonl")):
        k = sample_key(rec)
        if k not in best or not is_outcome(best[k]):  # the first outcome sticks
            best[k] = rec
    return best


def _case_at_commit(commit, path):
    """Load a case file as it was at a git commit (None if unavailable)."""
    import subprocess
    import yaml
    from .cases import Case, prompt_hash
    try:
        out = subprocess.run(["git", "-C", REPO, "show", f"{commit}:{path}"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    c = Case(yaml.safe_load(out.stdout))
    c.prompt_sha256 = prompt_hash(c)
    return c


def load_run_cases(manifest, allow_changed=False, log=print):
    """Cases as they were run. If a file changed since the run, grading is still faithful when only
    its checks changed (same prompt hash): those cases are listed as `checks_revised`. A changed
    prompt means the samples answer a different test: refuse unless --allow-changed."""
    cases, problems, revised = {}, [], []
    commit = (manifest.get("bench") or {}).get("commit")
    for item in manifest["suite"]["cases"]:
        path = os.path.join(REPO, item["path"]) if item.get("path") else None
        if not path or not os.path.exists(path):
            problems.append(f"{item['id']}: case file missing")
            continue
        c = load_case(path)
        if c.sha256 != item["sha256"]:
            old_prompt = item.get("prompt_sha256")
            if old_prompt is None and commit:
                old = _case_at_commit(commit, item["path"])
                old_prompt = old.prompt_sha256 if old is not None else None
            if old_prompt is not None and old_prompt == c.prompt_sha256:
                revised.append(item["id"])
            elif allow_changed:
                log(f"[warn] {item['id']}: the PROMPT changed since the run; grading anyway (--allow-changed)")
                revised.append(item["id"] + " (prompt changed!)")
            else:
                problems.append(f"{item['id']}: the prompt changed since the run (not just its checks)")
                continue
        cases[c.id] = c
    if problems:
        raise SystemExit("cannot grade faithfully:\n  " + "\n  ".join(problems) +
                         "\nRestore the case files from the run's bench commit, or pass --allow-changed.")
    if revised:
        log(f"[grade] checks revised since the run (prompts unchanged): {', '.join(revised)}")
    return cases, revised


def _same_host(manifest):
    from .manifest import host_info
    here, there = host_info(), manifest.get("host") or {}
    return all(here.get(k) == there.get(k) for k in ("cpu", "gpus", "os"))


def grade_run(run_dir, judges=None, allow_changed=False, trust_code=False, log=print):
    from .judge import build_body, verdict_key
    from .manifest import bench_commit
    manifest = load_manifest(run_dir)
    cases, revised = load_run_cases(manifest, allow_changed, log)
    samples = latest_samples(run_dir)
    family = manifest["model"]["profile"]["family"]
    judge = pick_judge(family, judges or [])
    if judges and judge is None:
        log(f"[warn] every configured judge is in the candidate's family ({family}); rubric checks stay pending")
    run_code = trust_code or _same_host(manifest)
    if not run_code:
        log("[warn] run generated on another host: code_tests are NOT executed (pass --trust-code to run them)")
    cache_path = os.path.join(run_dir, "judgments.jsonl")
    cache = {}
    for j in read_jsonl(cache_path):
        cache[(j["key"], j["check"], j["judge"], j.get("verdict_key"))] = j["result"]

    graded, to_judge = [], []
    for key, rec in sorted(samples.items()):
        case = cases.get(rec["case"])
        if case is None:
            continue
        lang = rec["lang"]
        excluded = None if is_outcome(rec) else f"generation error (not scored): {rec['error'][:160]}"
        results = []
        for idx, check in enumerate(case["checks"]):
            entry = {"idx": idx, "scorer": check["scorer"], "weight": check.get("weight", 1),
                     "name": check.get("name") or check["scorer"]}
            if excluded:
                res = {"score": None, "passed": None, "detail": excluded}
            else:
                res = score_check(check, rec, case, lang, run_code=run_code)
            if res is None:  # rubric -> judge
                if judge is not None:
                    body = build_body(judge, case, check, idx, rec, lang)
                    ck = (key, idx, judge.name, verdict_key(body, check))
                    if ck in cache:
                        res = cache[ck]
                    else:
                        to_judge.append((len(graded), len(results), case, check, idx, rec, lang, ck, body))
                        res = {"score": None, "passed": None, "detail": "pending judge"}
                else:
                    res = {"score": None, "passed": None, "detail": "no judge configured"}
            entry.update(res)
            results.append(entry)
        graded.append({"key": key, "case": rec["case"], "family": rec["family"], "item_type": rec["item_type"],
                       "review": rec["review"], "lang": lang, "sample": rec["sample"], "checks": results,
                       "error": rec.get("error"), "error_class": error_class(rec), "excluded": excluded,
                       "finish_reason": (rec.get("final") or {}).get("finish_reason"),
                       "completion_tokens": (rec.get("usage") or {}).get("completion_tokens"),
                       "latency_s": rec.get("latency_s"), "turns": len(rec.get("turns") or []),
                       "decode_tps": _decode_tps(rec), "tool_parse": (rec.get("final") or {}).get("tool_parse")})
    if to_judge:
        log(f"[grade] {len(to_judge)} rubric checks -> judge {judge.name} (family {judge.family})")
    for n, (gi, ci, case, check, idx, rec, lang, ck, body) in enumerate(to_judge, 1):
        res = judge_grade(judge, case, check, idx, rec, lang, body=body)
        if res.get("score") is not None:
            append_jsonl(cache_path, {"key": ck[0], "check": idx, "judge": judge.name, "verdict_key": ck[3],
                                      "prompt_sha256": PROMPT_SHA, "result": res, "at": now()})
        graded[gi]["checks"][ci].update(res)
        if n % 10 == 0 or n == len(to_judge):
            log(f"  judged {n}/{len(to_judge)}")
    for g in graded:
        g["pending"] = not g["excluded"] and any(c.get("score") is None for c in g["checks"])
        if g["excluded"] or g["pending"]:
            g["score"], g["passed"] = None, None  # never let a half-graded sample into the means
            continue
        w = sum(c["weight"] for c in g["checks"])
        g["score"] = round(sum(c["score"] * c["weight"] for c in g["checks"]) / w, 4) if w else None
        g["passed"] = all(c["passed"] for c in g["checks"])
    out = os.path.join(run_dir, "grades.jsonl")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8"):
        pass
    for g in graded:
        append_jsonl(tmp, g)
    os.replace(tmp, out)
    meta = {"graded_at": now(), "grader": bench_commit(), "judge": judge.describe() if judge else None,
            "judges_configured": [j.describe() for j in (judges or [])],
            "n_samples": len(graded), "n_pending": sum(1 for g in graded if g["pending"]),
            "n_excluded": sum(1 for g in graded if g["excluded"]), "checks_revised": revised,
            "code_executed": run_code, "allow_changed": allow_changed}
    write_json(os.path.join(run_dir, "grades-meta.json"), meta)
    log(f"[grade] {len(graded)} samples graded, {meta['n_pending']} pending, {meta['n_excluded']} excluded -> {out}")
    return graded, meta


def _decode_tps(rec):
    n = sum((t.get("timings") or {}).get("predicted_n") or 0 for t in rec.get("turns") or [])
    tps = [(t.get("timings") or {}).get("predicted_per_second") for t in rec.get("turns") or []]
    tps = [x for x in tps if x]
    if not tps:
        return None
    return round(sum(tps) / len(tps), 2) if n else None


def load_grades(run_dir):
    return read_jsonl(os.path.join(run_dir, "grades.jsonl"))
