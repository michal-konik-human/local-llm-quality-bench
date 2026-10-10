"""Statistics for small, clustered eval sets.

* Per-item first: each (case, language) item's k samples are averaged before anything else,
  so an item with more samples does not weigh more.
* Cluster bootstrap 95% CI: clusters are cases (the PL and EN versions of one task are
  correlated, so they are resampled together). The RNG is seeded, so CIs are reproducible.
* pass^k (all k attempts succeed) and pass@k use the unbiased estimators from n >= k samples.
* worst@k is the expected minimum score over k samples (HealthBench-style reliability).
"""

import random
from math import comb

BOOT = 2000


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def pass_hat_k(n, c, k):
    """P(all k of k draws pass) given c passes in n samples (without replacement)."""
    if n < k:
        return None
    return comb(c, k) / comb(n, k)


def pass_at_k(n, c, k):
    if n < k:
        return None
    return 1.0 - comb(n - c, k) / comb(n, k)


def worst_at_k(scores, k):
    """Expected minimum of k scores drawn without replacement from the item's samples."""
    s = sorted(x for x in scores if x is not None)
    n = len(s)
    if n < k or k < 1:
        return None
    total = comb(n, k)
    return sum(s[i] * comb(n - i - 1, k - 1) for i in range(n)) / total


def items(grades):
    """Group graded samples into items: {(case, lang): {...}}."""
    out = {}
    for g in grades:
        it = out.setdefault((g["case"], g["lang"]), {"case": g["case"], "lang": g["lang"], "family": g["family"],
                                                     "item_type": g["item_type"], "scores": [], "passes": []})
        it["scores"].append(g.get("score"))
        it["passes"].append(g.get("passed"))
    for it in out.values():
        it["mean"] = mean(it["scores"])
        decided = [p for p in it["passes"] if p is not None]
        it["n"] = len(decided)
        it["c"] = sum(1 for p in decided if p)
    return out


def bootstrap_ci(clustered, stat=None, seed=0, boot=BOOT):
    """clustered: {cluster_id: [values]} -> (point, lo, hi). stat defaults to mean of all values."""
    clustered = {k: [v for v in vs if v is not None] for k, vs in clustered.items()}
    clustered = {k: vs for k, vs in clustered.items() if vs}
    if not clustered:
        return None, None, None
    stat = stat or (lambda vals: sum(vals) / len(vals))
    keys = sorted(clustered)
    point = stat([v for k in keys for v in clustered[k]])
    if len(keys) < 2:
        return point, None, None
    rng = random.Random(seed)
    reps = []
    for _ in range(boot):
        vals = []
        for _ in keys:
            vals += clustered[keys[rng.randrange(len(keys))]]
        reps.append(stat(vals))
    reps.sort()
    return point, reps[int(0.025 * boot)], reps[int(0.975 * boot) - 1]


def summarize(item_list, k_rel=None):
    """Mean score with cluster CI, plus pass^1/pass^k and worst@k, over a list of items."""
    clustered = {}
    for it in item_list:
        if it["mean"] is not None:
            clustered.setdefault(it["case"], []).append(it["mean"])
    point, lo, hi = bootstrap_ci(clustered)
    ns = [it["n"] for it in item_list if it["n"]]
    k = k_rel or (min(ns) if ns else 0)
    p1 = mean([it["c"] / it["n"] for it in item_list if it["n"]])
    pk = mean([pass_hat_k(it["n"], it["c"], k) for it in item_list if it["n"] >= k]) if k > 1 else None
    wk = mean([worst_at_k(it["scores"], k) for it in item_list]) if k > 1 else None
    return {"mean": point, "ci": [lo, hi], "n_items": sum(len(v) for v in clustered.values()), "n_cases": len(clustered),
            "pass1": p1, "pass_k": pk, "worst_k": wk, "k": k}


def paired_diff(items_a, items_b, seed=0):
    """Mean difference A-B over items present in both, with a cluster bootstrap CI."""
    common = sorted(set(items_a) & set(items_b))
    clustered = {}
    for key in common:
        a, b = items_a[key]["mean"], items_b[key]["mean"]
        if a is not None and b is not None:
            clustered.setdefault(key[0], []).append(a - b)
    point, lo, hi = bootstrap_ci(clustered, seed=seed)
    return {"diff": point, "ci": [lo, hi], "n_items": sum(len(v) for v in clustered.values())}


def lang_gap(item_map):
    """PL minus EN on parallel cases only (paired per case)."""
    clustered = {}
    for (case, lang), it in item_map.items():
        if lang != "pl" or it["item_type"] != "parallel":
            continue
        en = item_map.get((case, "en"))
        if en and it["mean"] is not None and en["mean"] is not None:
            clustered[case] = [it["mean"] - en["mean"]]
    point, lo, hi = bootstrap_ci(clustered)
    return {"gap": point, "ci": [lo, hi], "n_cases": len(clustered)}


def cohen_kappa(pairs):
    """pairs: [(human_label, judge_label)] with labels in a small set."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if not n:
        return None, None
    labels = sorted({x for p in pairs for x in p})
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == l for a, _ in pairs) / n) * (sum(b == l for _, b in pairs) / n) for l in labels)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return po, kappa
