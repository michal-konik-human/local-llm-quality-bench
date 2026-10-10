"""Calibrate the LLM judge against human labels.

  qbench calibrate export RUN [RUN...] --n 40      -> judges/calibration/todo-<date>.yaml
  (a human fills in `human:` for every rubric item, without looking at the judge's verdicts)
  qbench calibrate import judges/calibration/todo-<date>.yaml
                                                    -> judges/calibration/<date>-<judge>.json

The export hides the judge's verdicts, so the human labels are independent.
"""

import datetime as dt
import json
import os
import random

import yaml

from .grade import load_grades, load_run_cases
from .manifest import load_manifest
from .stats import cohen_kappa
from .util import REPO, RESULTS_DIR, RUNS_DIR, read_jsonl, write_json

CALIB_DIR = os.path.join(RESULTS_DIR, "calibration")


def export(run_dirs, n=40, seed=0):
    pool = []
    for rd in run_dirs:
        manifest = load_manifest(rd)
        cases, _ = load_run_cases(manifest, allow_changed=True, log=lambda *_: None)
        samples = {f"{r['case']}|{r['lang']}|{r['sample']}": r for r in read_jsonl(os.path.join(rd, "samples.jsonl"))}
        for g in load_grades(rd):
            for c in g["checks"]:
                if c["scorer"] == "rubric" and c.get("verdicts"):
                    pool.append((rd, g, c, cases[g["case"]], samples.get(g["key"])))
    rng = random.Random(seed)
    by_lang = {"pl": [p for p in pool if p[1]["lang"] == "pl"], "en": [p for p in pool if p[1]["lang"] == "en"]}
    picked = []
    for lang, lst in by_lang.items():
        rng.shuffle(lst)
        lst.sort(key=lambda p: p[2].get("score") or 0)  # spread over the score range
        step = max(1, len(lst) // max(1, n // 2))
        picked += lst[::step][: n // 2]
    items = []
    for rd, g, c, case, rec in picked:
        check = case["checks"][c["idx"]]
        items.append({
            "run": os.path.basename(os.path.normpath(rd)), "key": g["key"], "check": c["idx"], "judge": c.get("judge"),
            "lang": g["lang"],
            "task": case.lang(g["lang"])["messages"][-1]["content"],
            "answer": ((rec or {}).get("final") or {}).get("content", ""),
            "items": [{"id": it["id"], "kind": "defect" if it["weight"] < 0 else "quality",
                       "criterion": it["criterion"] if isinstance(it["criterion"], str) else it["criterion"].get(g["lang"])}
                      for it in check["items"]],
            "human": {it["id"]: None for it in check["items"]},
        })
    os.makedirs(CALIB_DIR, exist_ok=True)
    path = os.path.join(CALIB_DIR, f"todo-{dt.date.today().isoformat()}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Fill every `human:` value with met / not_met / unknown. For DEFECT items, met = the defect is present.\n"
                "# Do not look at the judge's verdicts first. Then run: qbench calibrate import <this file>\n")
        yaml.safe_dump(items, f, allow_unicode=True, sort_keys=False, width=110)
    return path, len(items)


def import_labels(path):
    with open(path, encoding="utf-8") as f:
        items = yaml.safe_load(f)
    pairs, by_lang, judges = [], {"pl": [], "en": []}, set()
    for it in items:
        rd = os.path.join(RUNS_DIR, it["run"])
        if not os.path.isdir(rd):
            rd = os.path.join(RESULTS_DIR, it["run"])
        verdicts = None
        for g in load_grades(rd):
            if g["key"] == it["key"]:
                verdicts = g["checks"][it["check"]].get("verdicts")
        if not verdicts:
            continue
        judges.add(it.get("judge"))
        for item_id, human in (it.get("human") or {}).items():
            if human in ("met", "not_met") and item_id in verdicts:
                p = (human, verdicts[item_id]["verdict"])
                pairs.append(p)
                by_lang[it["lang"]].append(p)
    acc, kappa = cohen_kappa(pairs)
    res = {"date": dt.date.today().isoformat(), "source": os.path.basename(path), "n": len(pairs),
           "accuracy": acc, "kappa": kappa, "judges": sorted(j for j in judges if j),
           "by_lang": {l: dict(zip(("accuracy", "kappa"), cohen_kappa(v)), n=len(v)) for l, v in by_lang.items()}}
    outs = []
    for j in res["judges"] or ["unknown"]:
        out = os.path.join(CALIB_DIR, f"{res['date']}-{j}.json")
        write_json(out, res)
        outs.append(out)
    return res, outs
