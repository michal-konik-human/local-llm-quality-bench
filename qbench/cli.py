"""qbench command line. Run `python3 -m qbench --help`."""

import argparse
import datetime as dt
import json
import os
import sys

from .cases import LANGS, load_cases, validate_all
from .util import REPO, RESULTS_DIR, RUNS_DIR


def _run_dir(arg):
    for base in ("", RUNS_DIR, RESULTS_DIR):
        p = os.path.join(base, arg) if base else arg
        if os.path.isfile(os.path.join(p, "manifest.json")):
            return p
    raise SystemExit(f"no run directory {arg!r} (looked in ., {RUNS_DIR}, {RESULTS_DIR})")


def cmd_validate(a):
    from .profiles import list_profiles, load_profile
    cases = load_cases(a.paths or None)
    errs = validate_all(cases)
    for name in list_profiles():
        try:
            load_profile(name)
        except (ValueError, FileNotFoundError) as e:
            errs.append(f"model profile {name}: {e}")
    if errs:
        print("\n".join(errs))
        print(f"INVALID: {len(errs)} problems")
        return 1
    by_fam = {}
    for c in cases:
        by_fam.setdefault(c["family"], []).append(c)
    items = sum(len(c.langs()) for c in cases)
    print(f"VALID: {len(cases)} cases, {items} items (case x language)")
    for fam, cs in sorted(by_fam.items()):
        print(f"  {fam:22s} {len(cs):3d} cases  " + " ".join(sorted({c['item_type'] for c in cs})))
    return 0


def cmd_list(a):
    from .profiles import list_profiles, load_profile, preset
    print("Model profiles (models/*.yaml):")
    for name in list_profiles():
        p = load_profile(name)
        print(f"  {name:28s} family={p['family']:10s} presets: {', '.join(p['presets'])}")
    print("\nRuns:")
    for base, d in (("runs", RUNS_DIR), ("results", RESULTS_DIR)):
        for r in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if os.path.exists(os.path.join(d, r, "manifest.json")):
                graded = os.path.exists(os.path.join(d, r, "grades.jsonl"))
                print(f"  {base}/{r}{'  (graded)' if graded else ''}")
    return 0


def cmd_run(a):
    from .profiles import load_profile
    from .runner import run_suite
    prof = load_profile(a.model)
    cases = load_cases(a.cases or None, families=a.family, ids=a.case)
    errs = validate_all(cases)
    if errs:
        print("\n".join(errs))
        raise SystemExit("fix invalid cases first (qbench validate)")
    if not cases:
        raise SystemExit("no cases selected")
    preset_name = a.preset or prof.get("default_preset") or next(iter(prof["presets"]))
    run_id = a.run_id or f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{prof['name']}-{preset_name}"
    run_dir = a.out or os.path.join(RUNS_DIR, run_id)
    run_suite(prof, preset_name, cases, a.k, run_dir, langs=a.lang or LANGS, argv=sys.argv)
    print(run_dir)
    return 0


def _judges(a):
    if a.no_judge:
        return []
    from .judge import Judge, load_judge_config
    cfg = load_judge_config(a.judge_config)
    names = a.judge or cfg.get("judges") or []
    return [Judge(n, cfg) for n in names]


def cmd_grade(a):
    from .grade import grade_run
    judges = _judges(a)
    for r in a.runs:
        grade_run(_run_dir(r), judges=judges, allow_changed=a.allow_changed, trust_code=a.trust_code)
    return 0


def cmd_report(a):
    from .report import publish, update_leaderboard, write_report
    for r in a.runs:
        rd = _run_dir(r)
        if a.publish:
            dest = publish(rd)
            print(f"published -> {dest}/report.md")
        else:
            write_report(rd)
            print(f"{os.path.join(rd, 'report.md')}")
    if a.publish or not a.runs:
        update_leaderboard()
        print(f"{os.path.join(RESULTS_DIR, 'leaderboard.md')} updated")
    return 0


def cmd_compare(a):
    from .grade import load_grades
    from .report import pct_ci
    from .stats import items, paired_diff
    A, B = _run_dir(a.a), _run_dir(a.b)
    ia, ib = items(load_grades(A)), items(load_grades(B))
    print(f"A = {A}\nB = {B}\n")
    print(f"{'slice':24s} {'A - B (points, 95% CI)':>28s}  n")
    slices = [("all", lambda k, it: True)] + [(f"lang={l}", (lambda l: lambda k, it: k[1] == l)(l)) for l in LANGS]
    fams = sorted({it["family"] for it in ia.values()})
    slices += [(f, (lambda f: lambda k, it: it["family"] == f)(f)) for f in fams]
    for name, pred in slices:
        sa = {k: v for k, v in ia.items() if pred(k, v)}
        sb = {k: v for k, v in ib.items() if pred(k, v)}
        d = paired_diff(sa, sb)
        print(f"{name:24s} {pct_ci({'mean': d['diff'], 'ci': d['ci']}, signed=True):>28s}  {d['n_items']}")
    print("\nA difference is credible only when its CI excludes 0.")
    return 0


def cmd_replay(a):
    from .client import Client
    from .grade import latest_samples
    rd = _run_dir(a.run)
    key = f"{a.case}|{a.lang}|{a.sample}"
    rec = latest_samples(rd).get(key)
    if not rec:
        raise SystemExit(f"no sample {key} in {rd}")
    body = dict(rec["request"])
    if a.model_id:
        body["model"] = a.model_id
    if rec.get("env_state") is not None:
        print("note: this is a multi-turn tool case; replay re-sends the FIRST request only "
              "(use `qbench run --case ...` to replay the whole loop)\n", file=sys.stderr)
    resp = Client(a.base_url).chat(body)
    from .adapters import normalize
    norm = normalize(resp, tools_offered=bool(body.get("tools")))
    orig = rec["turns"][0] if rec.get("turns") else {}

    def calls(lst):
        return [(c.get("name"), c.get("arguments")) for c in lst or []]
    same = orig.get("content") == norm["content"] and calls(orig.get("tool_calls")) == calls(norm["tool_calls"])
    print(json.dumps({"request_seed": body.get("seed"),
                      "original": {"content": orig.get("content"), "tool_calls": orig.get("tool_calls")},
                      "replay": {"content": norm["content"], "tool_calls": norm["tool_calls"]},
                      "identical": same}, ensure_ascii=False, indent=2))
    return 0


def cmd_calibrate(a):
    from .calibrate import export, import_labels
    if a.action == "export":
        path, n = export([_run_dir(r) for r in a.items], n=a.n)
        print(f"wrote {n} items to {path}; fill in `human:` then run `qbench calibrate import {path}`")
    else:
        res, outs = import_labels(a.items[0])
        print(json.dumps(res, indent=2))
        print("saved:", ", ".join(outs))
    return 0


def cmd_review(a):
    """Print cases still pending review, or freeze one after a human has checked it."""
    cases = load_cases()
    if a.freeze:
        import re
        for c in cases:
            if c.id in a.freeze:
                txt = open(c.path, encoding="utf-8").read()
                txt2 = re.sub(r"^review: pending-owner$", "review: frozen", txt, count=1, flags=re.M)
                with open(c.path, "w", encoding="utf-8") as f:
                    f.write(txt2)
                print(f"frozen: {c.id}")
        return 0
    pending = [c for c in cases if c["review"] != "frozen"]
    for c in pending:
        print(f"{c['family']:22s} {c.id:45s} {os.path.relpath(c.path, REPO)}")
    print(f"\n{len(pending)} of {len(cases)} cases pending review. Freeze with: qbench review --freeze <id> [...]")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="qbench", description="Bilingual (PL/EN) quality evals for local LLMs.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("validate", help="validate case files and model profiles")
    s.add_argument("paths", nargs="*")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("list", help="list model profiles and runs")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("run", help="generate samples for one model + preset")
    s.add_argument("--model", required=True, help="model profile name (models/<name>.yaml) or path")
    s.add_argument("--preset", help="preset name (default: the profile's default_preset)")
    s.add_argument("--k", type=int, default=5, help="samples per item (default 5; use 3 for screening)")
    s.add_argument("--lang", action="append", choices=LANGS, help="restrict to a language (repeatable)")
    s.add_argument("--family", action="append", help="restrict to a case family (repeatable)")
    s.add_argument("--case", action="append", help="restrict to a case id (repeatable)")
    s.add_argument("--cases", action="append", help="case files/dirs (default: cases/)")
    s.add_argument("--run-id", help="run id (default: <timestamp>-<model>-<preset>); reuse one to resume")
    s.add_argument("--out", help="run directory (default: runs/<run-id>)")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("grade", help="score a run (deterministic scorers + LLM judge)")
    s.add_argument("runs", nargs="+")
    s.add_argument("--judge", action="append", help="judge model profile(s), in preference order")
    s.add_argument("--judge-config", help="judge config (default judges/default.yaml)")
    s.add_argument("--no-judge", action="store_true", help="skip rubric checks (they stay pending)")
    s.add_argument("--allow-changed", action="store_true",
                   help="grade even if a case's PROMPT changed since the run (changed checks alone are always fine)")
    s.add_argument("--trust-code", action="store_true",
                   help="execute code_tests for a run generated on another host (runs that run's model output)")
    s.set_defaults(fn=cmd_grade)

    s = sub.add_parser("report", help="write report.md/summary.json; --publish copies into $QBENCH_RESULTS + leaderboard")
    s.add_argument("runs", nargs="*")
    s.add_argument("--publish", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("compare", help="paired difference between two graded runs (same suite)")
    s.add_argument("a")
    s.add_argument("b")
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("replay", help="re-send one recorded request to any OpenAI-compatible server")
    s.add_argument("run")
    s.add_argument("case")
    s.add_argument("lang", choices=LANGS)
    s.add_argument("sample", type=int)
    s.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    s.add_argument("--model-id", help="override the model id in the request")
    s.set_defaults(fn=cmd_replay)

    s = sub.add_parser("calibrate", help="judge calibration: export a labelling form / import human labels")
    s.add_argument("action", choices=("export", "import"))
    s.add_argument("items", nargs="+", help="export: run dirs; import: the filled YAML file")
    s.add_argument("--n", type=int, default=40)
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("review", help="list cases pending native-speaker review, or freeze them")
    s.add_argument("--freeze", nargs="+")
    s.set_defaults(fn=cmd_review)

    a = p.parse_args(argv)
    return a.fn(a) or 0
