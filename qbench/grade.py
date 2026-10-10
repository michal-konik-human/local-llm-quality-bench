"""Grade a run: deterministic scorers for every check, the LLM judge for rubric checks.

Grading never touches samples.jsonl (generate once, grade many). Judge verdicts are cached
in judgments.jsonl keyed by (sample, check, judge, judge prompt hash), so re-grading after a
scorer fix is instant and only new or changed rubric checks reach the judge.
"""

import os

from .cases import load_case
from .judge import PROMPT_SHA, grade as judge_grade, pick_judge
from .manifest import load_manifest, now
from .scorers import score_check
from .util import REPO, append_jsonl, read_jsonl, write_json


def sample_key(rec):
    return f"{rec['case']}|{rec['lang']}|{rec['sample']}"


def latest_samples(run_dir):
    """Latest record per (case, lang, sample); a successful record beats a later failed retry."""
    best = {}
    for rec in read_jsonl(os.path.join(run_dir, "samples.jsonl")):
        k = sample_key(rec)
        if k not in best or not rec.get("error") or best[k].get("error"):
            best[k] = rec
    return best


def load_run_cases(manifest, allow_changed=False, log=print):
    cases, problems = {}, []
    for item in manifest["suite"]["cases"]:
        path = os.path.join(REPO, item["path"]) if item.get("path") else None
        if not path or not os.path.exists(path):
            problems.append(f"{item['id']}: case file missing")
            continue
        c = load_case(path)
        if c.sha256 != item["sha256"]:
            msg = f"{item['id']}: case file changed since the run (sha256 differs)"
            if not allow_changed:
                problems.append(msg)
                continue
            log(f"[warn] {msg}; grading with the CURRENT file because --allow-changed was given")
        cases[c.id] = c
    if problems and not allow_changed:
        raise SystemExit("cannot grade faithfully:\n  " + "\n  ".join(problems) +
                         "\nRestore the case files from the run's bench commit, or pass --allow-changed.")
    return cases


def grade_run(run_dir, judges=None, allow_changed=False, log=print):
    manifest = load_manifest(run_dir)
    cases = load_run_cases(manifest, allow_changed, log)
    samples = latest_samples(run_dir)
    family = manifest["model"]["profile"]["family"]
    judge = pick_judge(family, judges or [])
    if judges and judge is None:
        log(f"[warn] every configured judge is in the candidate's family ({family}); rubric checks stay pending")
    cache_path = os.path.join(run_dir, "judgments.jsonl")
    cache = {}
    for j in read_jsonl(cache_path):
        cache[(j["key"], j["check"], j["judge"], j["prompt_sha256"])] = j["result"]

    graded = []
    to_judge = []
    for key, rec in sorted(samples.items()):
        case = cases.get(rec["case"])
        if case is None:
            continue
        lang = rec["lang"]
        results = []
        for idx, check in enumerate(case["checks"]):
            res = score_check(check, rec, case, lang)
            entry = {"idx": idx, "scorer": check["scorer"], "weight": check.get("weight", 1),
                     "name": check.get("name") or check["scorer"]}
            if res is None:  # rubric
                if rec.get("error") and not rec.get("final"):
                    res = {"score": 0.0, "passed": False, "detail": "generation failed"}
                elif judge is not None:
                    ck = (key, idx, judge.name, PROMPT_SHA)
                    if ck in cache:
                        res = cache[ck]
                    else:
                        to_judge.append((len(graded), len(results), case, check, idx, rec, lang, ck))
                        res = {"score": None, "passed": None, "detail": "pending judge"}
                else:
                    res = {"score": None, "passed": None, "detail": "no judge configured"}
            entry.update(res)
            results.append(entry)
        graded.append({"key": key, "case": rec["case"], "family": rec["family"], "item_type": rec["item_type"],
                       "review": rec["review"], "lang": lang, "sample": rec["sample"], "checks": results,
                       "error": rec.get("error"), "completion_tokens": (rec.get("usage") or {}).get("completion_tokens"),
                       "latency_s": rec.get("latency_s"),
                       "decode_tps": _decode_tps(rec), "tool_parse": (rec.get("final") or {}).get("tool_parse")})
    if to_judge:
        log(f"[grade] {len(to_judge)} rubric checks -> judge {judge.name} (family {judge.family})")
    for n, (gi, ci, case, check, idx, rec, lang, ck) in enumerate(to_judge, 1):
        res = judge_grade(judge, case, check, idx, rec, lang)
        if res.get("score") is not None:
            append_jsonl(cache_path, {"key": ck[0], "check": idx, "judge": judge.name,
                                      "prompt_sha256": PROMPT_SHA, "result": res, "at": now()})
        graded[gi]["checks"][ci].update(res)
        if n % 10 == 0 or n == len(to_judge):
            log(f"  judged {n}/{len(to_judge)}")
    for g in graded:
        scored = [c for c in g["checks"] if c.get("score") is not None]
        w = sum(c["weight"] for c in scored)
        g["score"] = round(sum(c["score"] * c["weight"] for c in scored) / w, 4) if w else None
        g["pending"] = any(c.get("score") is None for c in g["checks"])
        g["passed"] = None if g["pending"] else all(c["passed"] for c in g["checks"])
    out = os.path.join(run_dir, "grades.jsonl")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8"):
        pass
    for g in graded:
        append_jsonl(tmp, g)
    os.replace(tmp, out)
    meta = {"graded_at": now(), "judge": judge.describe() if judge else None,
            "judges_configured": [j.describe() for j in (judges or [])],
            "n_samples": len(graded), "n_pending": sum(1 for g in graded if g["pending"]),
            "allow_changed": allow_changed}
    write_json(os.path.join(run_dir, "grades-meta.json"), meta)
    log(f"[grade] {len(graded)} samples graded, {meta['n_pending']} pending -> {out}")
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
