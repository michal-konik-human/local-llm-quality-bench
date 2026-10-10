"""Turn graded runs into a per-run report, a summary.json, and the shared leaderboard."""

import glob
import json
import os
import shutil
import statistics

from .cases import FAMILIES
from .grade import load_grades
from .manifest import load_manifest, now
from .stats import bootstrap_ci, items as group_items, lang_gap, summarize
from .util import METHODOLOGY_URL, RESULTS_DIR, write_json

FAMILY_LABEL = {"tool_calling": "Tools", "agentic": "Agent", "web_shopping": "Shop",
                "instruction_following": "Instr.", "writing": "Writing", "reasoning": "Reason.", "coding": "Code"}
RELIABILITY_FAMILIES = ("tool_calling", "agentic", "web_shopping")
CALIB_DIR = os.path.join(RESULTS_DIR, "calibration")


def pct(x):
    return "–" if x is None else f"{100 * x:.0f}"


def spct(x):
    """Signed points for differences: +3, −2 (true minus sign), 0."""
    if x is None:
        return "–"
    v = round(100 * x)
    return f"+{v}" if v > 0 else (f"\u2212{-v}" if v < 0 else "0")


def pct_ci(s, signed=False):
    if not s or s.get("mean") is None:
        return "–"
    lo, hi = (s.get("ci") or [None, None])
    if signed:
        return spct(s["mean"]) + (f" ({spct(lo)} to {spct(hi)})" if lo is not None else "")
    return f"{pct(s['mean'])}" + (f" ({pct(lo)}–{pct(hi)})" if lo is not None else "")


def _macro(items_list):
    """Overall per language = mean of family means (every family weighs the same), cluster CI."""
    clustered = {}
    for it in items_list:
        if it["mean"] is not None:
            clustered.setdefault(it["case"], []).append((it["family"], it["mean"]))

    def stat(vals):
        fam = {}
        for f, v in vals:
            fam.setdefault(f, []).append(v)
        return sum(sum(v) / len(v) for v in fam.values()) / len(fam)
    point, lo, hi = bootstrap_ci(clustered, stat=stat)
    return {"mean": point, "ci": [lo, hi], "n_families": len({it["family"] for it in items_list})}


def latest_calibration(judge_name):
    files = sorted(glob.glob(os.path.join(CALIB_DIR, f"*-{judge_name}.json")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def summarize_run(run_dir):
    manifest = load_manifest(run_dir)
    grades = load_grades(run_dir)
    if not grades:
        raise SystemExit(f"{run_dir}: no grades.jsonl; run `qbench grade {run_dir}` first")
    meta_path = os.path.join(run_dir, "grades-meta.json")
    gmeta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
    imap = group_items(grades)
    prof = manifest["model"]["profile"]
    out = {"run_id": manifest["run_id"], "generated_at": now(), "k": manifest["k"],
           "model": {"name": prof["name"], "display": prof.get("display", prof["name"]), "family": prof["family"],
                     "quant": (prof.get("weights") or {}).get("quant"), "params": prof.get("params"),
                     "license": prof.get("license"), "repo": (prof.get("weights") or {}).get("repo"),
                     "perf": prof.get("perf")},
           "preset": {k: manifest["preset"].get(k) for k in ("name", "sampling", "max_tokens", "extra_body", "source")},
           "server_build": ((manifest.get("server") or {}).get("llama_cpp") or {}).get("build_info"),
           "sampling_mismatches": manifest.get("sampling_mismatches"),
           "judge": (gmeta.get("judge") or {}).get("judge"),
           "judge_calibration": None, "langs": {}}
    if out["judge"]:
        cal = latest_calibration(out["judge"])
        out["judge_calibration"] = {k: cal.get(k) for k in ("date", "n", "accuracy", "kappa", "by_lang")} if cal else None
    for lang in ("pl", "en"):
        its = [it for (c, l), it in imap.items() if l == lang]
        if not its:
            continue
        fams = {}
        for fam in FAMILIES:
            fi = [it for it in its if it["family"] == fam]
            if fi:
                fams[fam] = summarize(fi)
        out["langs"][lang] = {"overall": _macro(its), "families": fams,
                              "reliability": summarize([it for it in its if it["family"] in RELIABILITY_FAMILIES])
                              if any(it["family"] in RELIABILITY_FAMILIES for it in its) else None}
    out["lang_gap"] = lang_gap(imap)
    toks = [g["completion_tokens"] for g in grades if g.get("completion_tokens")]
    tps = [g["decode_tps"] for g in grades if g.get("decode_tps")]
    lat = [g["latency_s"] for g in grades if g.get("latency_s")]
    out["cost"] = {"median_completion_tokens": statistics.median(toks) if toks else None,
                   "median_decode_tps": round(statistics.median(tps), 1) if tps else None,
                   "median_latency_s": round(statistics.median(lat), 2) if lat else None}
    out["checks_revised"] = gmeta.get("checks_revised") or []
    out["grader"] = gmeta.get("grader")
    out["health"] = {"samples": len(grades), "errors": sum(1 for g in grades if g.get("error")),
                     "excluded": sum(1 for g in grades if g.get("excluded")),
                     "max_turns": sum(1 for g in grades if g.get("error_class") == "max_turns"),
                     "unparseable": sum(1 for g in grades if g.get("error_class") == "unparseable"),
                     "truncated": sum(1 for g in grades if g.get("finish_reason") == "length"),
                     "pending": sum(1 for g in grades if g.get("pending")),
                     "tool_parse_fallback": sum(1 for g in grades if g.get("tool_parse") == "fallback"),
                     "cases_pending_review": len({g["case"] for g in grades if g.get("review") != "frozen"}),
                     "cases": len({g["case"] for g in grades})}
    return out, manifest, grades, imap


def _failures(grades, per_family=2):
    worst = {}
    for g in grades:
        if g.get("score") is None or g.get("passed"):
            continue
        worst.setdefault(g["family"], []).append(g)
    lines = []
    for fam in FAMILIES:
        for g in sorted(worst.get(fam, []), key=lambda x: x["score"])[:per_family]:
            bad = [c for c in g["checks"] if not c.get("passed")]
            why = "; ".join(f"{c['name']}: {str(c.get('detail'))[:160]}" for c in bad[:2])
            lines.append(f"| {FAMILY_LABEL[fam]} | `{g['case']}` | {g['lang']} | #{g['sample']} | {pct(g['score'])} | {why.replace('|', '/')} |")
    return lines


def render_report(summary, manifest, grades):
    m, p = summary["model"], summary["preset"]
    L = [f"# Quality run `{summary['run_id']}`: {m['display']} / preset `{p['name']}`", ""]
    h = summary["health"]
    cal = summary["judge_calibration"]
    L += ["| | |", "|---|---|",
          f"| Model | {m['display']} ({m.get('params') or '?'}, {m.get('quant') or '?'}), family `{m['family']}`, license {m.get('license') or '?'} |",
          f"| Weights | {m.get('repo') or '–'} |",
          f"| Server | {summary.get('server_build') or 'not llama.cpp / unknown'} |",
          f"| Samplers sent | `{json.dumps(p['sampling'])}`, max_tokens {p['max_tokens']}"
          + (f", extra `{json.dumps(p['extra_body'], ensure_ascii=False)}`" if p.get("extra_body") else "") + " |",
          f"| Preset source | {p.get('source') or '–'} |",
          f"| Samples | k = {summary['k']} per item, {h['samples']} total; {h['excluded']} excluded (server/transport errors), "
          f"{h['max_turns']} ran out of turns, {h['unparseable']} unparseable output (rejected by the server's parser, scored 0), "
          f"{h['truncated']} hit max_tokens, {h['pending']} pending grades |",
          f"| Judge | {summary.get('judge') or 'none'}" + (f", calibrated κ = {cal['kappa']:.2f} on {cal['n']} labels" if cal and cal.get('kappa') is not None else ", **not yet calibrated against human labels**" if summary.get('judge') else "") + " |",
          f"| Suite | {manifest['suite']['n_cases']} cases, sha256 `{manifest['suite']['suite_sha256'][:16]}…`, bench commit `{(manifest['bench'].get('commit') or '?')[:10]}`{' (dirty)' if manifest['bench'].get('dirty') else ''} |",
          ""]
    if summary.get("checks_revised"):
        L += [f"> ✏️ Checks revised after generation (prompts unchanged, so the samples stay valid): "
              f"{', '.join('`' + c + '`' for c in summary['checks_revised'])}. Graded with bench commit "
              f"`{((summary.get('grader') or {}).get('commit') or '?')[:10]}`.", ""]
    if h["cases_pending_review"]:
        L += [f"> {h['cases_pending_review']} of {h['cases']} cases are still `review: pending-owner` "
              "(written by the bench author, not yet frozen by a native-speaker review).", ""]
    if summary.get("sampling_mismatches"):
        L += [f"> ⚠️ The server used different sampler values than requested: `{summary['sampling_mismatches']}`", ""]
    if h["tool_parse_fallback"]:
        L += [f"> ℹ️ {h['tool_parse_fallback']} samples had tool calls returned as text and parsed by qbench "
              "(the server's template parser did not recognise them). Scored normally, flagged here.", ""]
    L += ["## Scores (0–100, mean with 95% cluster-bootstrap CI)", "",
          "| Family | 🇵🇱 PL | 🇬🇧 EN | PL pass^1 | EN pass^1 | PL pass^k | EN pass^k | PL worst@k | EN worst@k |",
          "|---|---|---|---|---|---|---|---|---|"]
    pl, en = summary["langs"].get("pl", {}), summary["langs"].get("en", {})
    for fam in FAMILIES:
        a, b = (pl.get("families") or {}).get(fam), (en.get("families") or {}).get(fam)
        if not a and not b:
            continue
        L.append(f"| {FAMILY_LABEL[fam]} | {pct_ci(a)} | {pct_ci(b)} | {pct((a or {}).get('pass1'))} | "
                 f"{pct((b or {}).get('pass1'))} | {pct((a or {}).get('pass_k'))} | {pct((b or {}).get('pass_k'))} | "
                 f"{pct((a or {}).get('worst_k'))} | {pct((b or {}).get('worst_k'))} |")
    L.append(f"| **Overall** (mean of families) | **{pct_ci(pl.get('overall'))}** | **{pct_ci(en.get('overall'))}** | | | | | | |")
    L += ["", "_pass^1: share of samples that pass every check. pass^k: chance that all k samples of a task pass. "
          "worst@k: expected score of the worst of k samples._"]
    gap = summary["lang_gap"]
    L += ["", f"**PL − EN gap** on parallel tasks: {pct_ci({'mean': gap['gap'], 'ci': gap['ci']}, signed=True)} points "
          f"({gap['n_cases']} paired tasks; negative = worse in Polish).", ""]
    c = summary["cost"]
    L += [f"**Cost:** median {c['median_completion_tokens']} completion tokens per sample, "
          f"median decode {c['median_decode_tps']} tok/s, median latency {c['median_latency_s']} s per sample.", ""]
    fails = _failures(grades)
    if fails:
        L += ["## Worst failures (read the transcripts before trusting any number)", "",
              "| Family | Case | Lang | Sample | Score | Why |", "|---|---|---|---|---|---|"] + fails + [""]
    L += ["## Replicate", "", "Every sample in `samples.jsonl` holds the exact request body. To re-run one:", "",
          "```bash", f"python3 -m qbench replay {summary['run_id']} <case-id> <lang> <sample> --base-url http://127.0.0.1:8080/v1", "```", "",
          "Or re-run the whole configuration on your machine:", "",
          "```bash", f"python3 -m qbench run --model {m['name']} --preset {p['name']} --k {summary['k']}",
          "python3 -m qbench grade runs/<new-run-id>", "python3 -m qbench report runs/<new-run-id>", "```", "",
          "Expect overlapping confidence intervals, not identical tokens: GPU kernels, batch sizes and builds "
          "change floating-point results. See docs/REPLICATE.md.", ""]
    return "\n".join(L)


def write_report(run_dir):
    summary, manifest, grades, _ = summarize_run(run_dir)
    write_json(os.path.join(run_dir, "summary.json"), summary)
    md = render_report(summary, manifest, grades)
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return summary


def publish(run_dir):
    """Copy a graded run into <results>/<run_id>/ ($QBENCH_RESULTS, default ./results) and refresh the leaderboard."""
    summary = write_report(run_dir)
    dest = os.path.join(RESULTS_DIR, summary["run_id"])
    if os.path.abspath(dest) != os.path.abspath(run_dir):
        os.makedirs(dest, exist_ok=True)
        for name in ("manifest.json", "samples.jsonl", "grades.jsonl", "grades-meta.json", "judgments.jsonl",
                     "summary.json", "report.md"):
            src = os.path.join(run_dir, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dest, name))
    update_leaderboard()
    return dest


def update_leaderboard():
    rows = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*", "summary.json"))):
        with open(path, encoding="utf-8") as f:
            rows.append(json.load(f))
    fams = [f for f in FAMILIES if any(f in (r["langs"].get(l) or {}).get("families", {}) for r in rows for l in ("pl", "en"))]
    L = ["# Leaderboard", "",
         "Scores 0–100 with 95% CI (cluster bootstrap over tasks). **Overall** is the mean of family scores, "
         "so every family weighs the same. **Reliability** is pass^k on tool, agent and shopping tasks: the chance "
         "that *all k* attempts succeed, which is what an assistant you rely on needs. Configs whose CIs overlap are "
         "not distinguishable at this sample size.", "",
         f"_Generated {now()} from `results/*/summary.json` by `qbench report --publish`._", ""]
    for lang, flag in (("pl", "🇵🇱 Polish"), ("en", "🇬🇧 English")):
        lr = [r for r in rows if lang in r["langs"]]
        if not lr:
            continue
        lr.sort(key=lambda r: -(r["langs"][lang]["overall"]["mean"] or 0))
        L += [f"## {flag}", "",
              "| # | Model | Quant | Preset | **Overall** | " + " | ".join(FAMILY_LABEL[f] for f in fams)
              + " | Reliability pass^k | Tokens/answer | Decode tok/s | Run |",
              "|---|---|---|---|---|" + "---|" * len(fams) + "---|---|---|---|"]
        for i, r in enumerate(lr, 1):
            d = r["langs"][lang]
            rel = d.get("reliability") or {}
            cells = [pct((d["families"].get(f) or {}).get("mean")) for f in fams]
            L.append(f"| {i} | {r['model']['display']} | {r['model'].get('quant') or '–'} | `{r['preset']['name']}` | "
                     f"**{pct_ci(d['overall'])}** | " + " | ".join(cells) +
                     f" | {pct(rel.get('pass_k'))} (k={rel.get('k', '–')}) | {r['cost']['median_completion_tokens'] or '–'} | "
                     f"{r['cost']['median_decode_tps'] or '–'} | [{r['run_id']}]({r['run_id']}/report.md) |")
        L.append("")
    gaps = [r for r in rows if r.get("lang_gap", {}).get("gap") is not None]
    if gaps:
        L += ["## Polish vs English (same tasks, paired)", "", "| Model | Preset | PL − EN (points, 95% CI) | Paired tasks |",
              "|---|---|---|---|"]
        for r in sorted(gaps, key=lambda r: r["lang_gap"]["gap"]):
            g = r["lang_gap"]
            L.append(f"| {r['model']['display']} | `{r['preset']['name']}` | {pct_ci({'mean': g['gap'], 'ci': g['ci']}, signed=True)} | {g['n_cases']} |")
        L.append("")
    judges = sorted({(r.get("judge") or "none") for r in rows})
    L += [f"Judges used: {', '.join(judges)}. Rubric checks are judged by a model from a *different family* than the "
          f"candidate. Method: [local-llm-quality-bench METHODOLOGY]({METHODOLOGY_URL}).", ""]
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "leaderboard.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return rows

