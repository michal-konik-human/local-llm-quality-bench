"""The fixed scorer set. Every scorer returns {"score": 0..1, "passed": bool, "detail": str}.

Deterministic scorers run here. `rubric` returns None and is filled in by the judge step.
Each scorer reads the FINAL answer (reasoning stripped), so a model is graded on what it
told the user, not on what it thought.
"""

import re

from .cases import resolve
from .ifeval import run_rules
from .sandbox import extract_code, run_python
from .textutil import answer_line, contains, extract_json, matches, numbers
from .util import fold, nfc


ASK_RE = {"pl": r"(\?|\b(podaj|podać|proszę o|potwierdź|potwierdzić|wskaż|wskazać|daj znać|napisz,? (któr|jak|gdzie|czy))\b)",
          "en": r"(\?|\b(please (provide|confirm|tell|share|specify|send|let me know)|let me know|could you (provide|tell|share|confirm)|i('ll| will)? need (the|your|to know))\b)"}


def asks_user(text, lang):
    """A clarifying question, or an explicit request for the missing information."""
    return matches(ASK_RE.get(lang, ASK_RE["en"]), text or "")


def _res(score, passed, detail):
    return {"score": round(float(score), 4), "passed": bool(passed), "detail": detail}


def final_text(rec):
    return ((rec.get("final") or {}).get("content")) or ""


def _answer_text(rec, check):
    """Text to grade for short-answer scorers: the labelled answer line when present."""
    text = final_text(rec)
    if check.get("use_answer_line", True):
        line = answer_line(text)
        if line is not None:
            return line, "answer line"
        if check.get("require_answer_line"):
            return None, "no ANSWER:/ODPOWIEDŹ: line"
    return text, "whole reply"


# --- simple answers ---------------------------------------------------------

def score_exact(check, rec, case, lang):
    exp = resolve(check["expected"], lang)
    text, where = _answer_text(rec, check)
    if text is None:
        return _res(0, False, where)
    options = exp if isinstance(exp, list) else [exp]
    got = fold(text).strip(" .!*`\"'")
    # exact match, or the expected answer followed only by a parenthetical/explanation ("sobota (16.01.2027)")
    ok = any(got == fold(str(o)) or re.match(re.escape(fold(str(o))) + r"(?![\w:])[\s,;(–-]", got) for o in options)
    return _res(ok, ok, f"{where}: {text[:80]!r} vs {options}")


def score_numeric(check, rec, case, lang):
    exp = float(resolve(check["expected"], lang))
    tol = float(check.get("tol", 1e-6))
    rel_tol = check.get("rel_tol")
    text, where = _answer_text(rec, check)
    if text is None:
        return _res(0, False, where)
    nums = numbers(text)
    if where == "whole reply" and check.get("last_number_only", True) and nums:
        # without an answer line, only the final number counts (avoid credit for listing every candidate)
        last_tok = re.findall(r"[-−]?\d[\d .,]*", nfc(text))  # nfc() already mapped exotic spaces to " "
        nums = numbers(last_tok[-1]) if last_tok else nums
    ok = any(abs(n - exp) <= tol or (rel_tol and abs(n - exp) <= abs(exp) * float(rel_tol)) for n in nums)
    return _res(ok, ok, f"{where}: numbers {nums[:6]} vs {exp} ± {tol}")


def _match_parts(spec, text, lang):
    parts = []
    for p in resolve(spec.get("contains_all"), lang) or []:
        parts.append((contains(p, text), f"contains {p!r}"))
    any_ = resolve(spec.get("contains_any"), lang)
    if any_:
        parts.append((any(contains(p, text) for p in any_), f"contains any of {any_}"))
    for p in resolve(spec.get("not_contains"), lang) or []:
        parts.append((not contains(p, text), f"does not contain {p!r}"))
    for p in resolve(spec.get("regex"), lang) or []:
        parts.append((matches(p, text), f"matches /{p}/"))
    for p in resolve(spec.get("not_regex"), lang) or []:
        parts.append((not matches(p, text), f"does not match /{p}/"))
    return parts


def score_match(check, rec, case, lang):
    spec = resolve(check["expected"], lang)
    text = final_text(rec)
    parts = _match_parts(spec, text, lang)
    if not parts:
        return _res(0, False, "empty match spec")
    n = sum(ok for ok, _ in parts)
    return _res(n / len(parts), n == len(parts),
                "; ".join(("ok " if ok else "FAIL ") + d for ok, d in parts))


def score_answer(check, rec, case, lang):
    """Final choice of an agent task: which item it picked (by id/url) and at what number.
    The CHOICE:/WYBÓR: line is REQUIRED (unless the check sets require_answer_line: false),
    so a reply that lists every candidate cannot earn credit for the one it never chose."""
    spec = resolve(check["expected"], lang)
    text, where = _answer_text(rec, {"require_answer_line": True, **check})
    if text is None:
        return _res(0, False, where)
    parts = _match_parts(spec, text, lang)
    if "number" in spec:
        tol = float(spec.get("tol", 0.01))
        nums = numbers(text)
        parts.append((any(abs(n - float(spec["number"])) <= tol for n in nums), f"number {spec['number']} ± {tol}"))
    if "set" in spec:
        # the answer must list EXACTLY these items (no more, no fewer); items matched by token_regex
        pat = spec.get("token_regex", r"[A-Za-z]{1,4}-\d{1,4}")
        got = {fold(t) for t in re.findall(pat, nfc(text))}
        want = {fold(str(t)) for t in resolve(spec["set"], lang)}
        missing, extra = sorted(want - got), sorted(got - want)
        # partial credit = Jaccard overlap (each wrong inclusion costs as much as each omission); passed = exact set
        jac = len(want & got) / len(want | got) if (want | got) else 1.0
        parts.append((jac if spec.get("partial", True) else float(jac == 1.0),
                      f"set: missing {missing}, extra {extra} (overlap {jac:.2f})" if (missing or extra) else "set exact"))
    if not parts:
        return _res(0, False, "empty answer spec")
    n = sum(float(ok) for ok, _ in parts)
    return _res(n / len(parts), all(float(ok) == 1.0 for ok, _ in parts),
                f"{where}: " + "; ".join(("ok " if float(ok) == 1.0 else "FAIL ") + d for ok, d in parts))


# --- structured output --------------------------------------------------------

def validate_schema(obj, schema, path="$"):
    """A small JSON-Schema subset: type, enum, const, properties, required, additionalProperties,
    items, minItems, maxItems, minimum, maximum, minLength, maxLength, pattern."""
    errs = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    if t:
        allowed = t if isinstance(t, list) else [t]
        ok = False
        for a in allowed:
            if a == "integer":
                ok |= isinstance(obj, int) and not isinstance(obj, bool)
            elif a == "number":
                ok |= isinstance(obj, (int, float)) and not isinstance(obj, bool)
            else:
                ok |= isinstance(obj, types.get(a, object))
        if not ok:
            return [f"{path}: expected {t}, got {type(obj).__name__}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if "const" in schema and obj != schema["const"]:
        errs.append(f"{path}: {obj!r} != {schema['const']!r}")
    if isinstance(obj, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in obj:
                errs.append(f"{path}: missing required '{r}'")
        if schema.get("additionalProperties") is False:
            for k in obj:
                if k not in props:
                    errs.append(f"{path}: unexpected property '{k}'")
        for k, sub in props.items():
            if k in obj:
                errs += validate_schema(obj[k], sub, f"{path}.{k}")
    if isinstance(obj, list):
        if "minItems" in schema and len(obj) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, it in enumerate(obj):
                errs += validate_schema(it, schema["items"], f"{path}[{i}]")
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: {obj} < {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append(f"{path}: {obj} > {schema['maximum']}")
    if isinstance(obj, str):
        if "minLength" in schema and len(obj) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(obj) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            errs.append(f"{path}: does not match {schema['pattern']}")
    return errs


def score_json_schema(check, rec, case, lang):
    text = final_text(rec)
    try:
        obj = extract_json(text)
    except ValueError as e:
        return _res(0, False, str(e))
    errs = validate_schema(obj, resolve(check["expected"], lang))
    values = check.get("values")  # optional exact field checks: {"field.path": value}
    for path, want in (resolve(values, lang) or {}).items():
        cur = obj
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        want_ = resolve(want, lang)
        if isinstance(want_, dict) and "regex" in want_:
            if not (isinstance(cur, str) and matches(want_["regex"], cur)):
                errs.append(f"{path}: {cur!r} !~ /{want_['regex']}/")
        elif isinstance(want_, dict) and "numeric" in want_:
            if not (isinstance(cur, (int, float)) and abs(cur - want_["numeric"]) <= want_.get("tol", 1e-6)):
                errs.append(f"{path}: {cur!r} != {want_['numeric']}")
        elif (fold(str(cur)) if isinstance(cur, str) else cur) != (fold(want_) if isinstance(want_, str) else want_):
            errs.append(f"{path}: {cur!r} != {want_!r}")
    if check.get("bare") and text.strip()[:1] not in "{[":
        errs.append("JSON not bare (wrapped in prose or a code fence)")
    return _res(not errs, not errs, "; ".join(errs[:6]) or "valid")


def score_ifeval(check, rec, case, lang):
    frac, ok, details = run_rules(final_text(rec), resolve(check["rules"], lang), lang)
    return _res(frac, ok, "; ".join(details))


# --- tool calls -------------------------------------------------------------

def _arg_ok(rule, value, lang):
    rule = resolve(rule, lang)
    if isinstance(rule, dict):
        rule = {k: resolve(v, lang) for k, v in rule.items()}  # e.g. {regex: {pl: .., en: ..}}
        if rule.get("absent"):
            return value is None
        if value is None:
            return False
        if rule.get("any"):
            return True
        if "one_of" in rule:
            return any(_arg_ok(o, value, lang) for o in rule["one_of"])
        if "regex" in rule:
            return isinstance(value, str) and matches(rule["regex"], value)
        if "contains" in rule:
            return isinstance(value, str) and all(contains(c, value) for c in
                                                  (rule["contains"] if isinstance(rule["contains"], list) else [rule["contains"]]))
        if "numeric" in rule:
            return (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and abs(float(value) - float(rule["numeric"])) <= float(rule.get("tol", 1e-6)))
        if "list_of" in rule:
            # every expected element (a dict of per-field rules) must match a distinct element of the list
            if not isinstance(value, list):
                return False
            if rule.get("exact_len", True) and len(value) != len(rule["list_of"]):
                return False
            free = list(value)
            for want in rule["list_of"]:
                hit = next((v for v in free if isinstance(v, dict) and
                            all(_arg_ok(r, v.get(k), lang) for k, r in want.items())), None)
                if hit is None:
                    return False
                free.remove(hit)
            return True
        if "set" in rule:
            return isinstance(value, list) and {fold(str(v)) for v in value} == {fold(str(v)) for v in rule["set"]}
        if "subset" in rule:
            return isinstance(value, list) and {fold(str(v)) for v in rule["subset"]} <= {fold(str(v)) for v in value}
        return value == rule
    if isinstance(rule, str):
        return isinstance(value, str) and fold(value) == fold(rule)
    if isinstance(rule, bool) or rule is None:
        return value is rule
    if isinstance(rule, (int, float)):
        # the schema says number: a string "21.5" or a boolean is a type error (BFCL-style)
        return isinstance(value, (int, float)) and not isinstance(value, bool) and abs(float(value) - float(rule)) < 1e-6
    return value == rule


def _call_score(exp, call, lang, allow_extra_args):
    if call.get("name") != exp["name"]:
        return 0.0, f"name {call.get('name')} != {exp['name']}"
    if call.get("error"):
        return 0.0, f"{exp['name']}: {call['error']}"
    args = call.get("arguments") or {}
    rules = exp.get("args") or {}
    bad = [k for k, r in rules.items() if not _arg_ok(r, args.get(k), lang)]
    extra = [] if allow_extra_args else [k for k in args if k not in rules and k not in (exp.get("optional") or [])]
    n = max(len(rules), 1)
    score = (n - len(bad)) / n if rules else 1.0
    if extra:
        score *= 0.5
    det = f"{exp['name']}: " + (f"wrong args {bad} (got { {k: args.get(k) for k in bad} })" if bad else "args ok") \
        + (f", unexpected args {extra}" if extra else "")
    return score, det


def score_tool_call(check, rec, case, lang):
    exp = resolve(check["expected"], lang)
    scope = check.get("scope", "first")
    turns = rec.get("turns") or []
    calls = (turns[0]["tool_calls"] if turns else []) if scope == "first" else (rec.get("all_tool_calls") or [])
    parse = turns[0]["tool_parse"] if turns else "none"
    note = " [text-parsed tool call]" if parse == "fallback" else ""
    if exp.get("none") or exp.get("ask"):
        if calls:
            return _res(0, False, f"called {[c.get('name') for c in calls]} but should not call any tool{note}")
        if not final_text(rec).strip():
            return _res(0, False, f"no tool call, but also no reply at all (finish_reason={(rec.get('final') or {}).get('finish_reason')})")
        if exp.get("ask"):
            text = final_text(rec)
            asked = asks_user(text, lang) and (not exp.get("ask_regex") or matches(resolve(exp["ask_regex"], lang), text))
            return _res(asked, asked, "asked a clarifying question" if asked else f"did not ask: {text[:120]!r}")
        return _res(1, True, "correctly called no tool")
    want = exp["calls"]
    if not calls:
        return _res(0, False, f"no tool call (wanted {[w['name'] for w in want]}); reply: {final_text(rec)[:100]!r}")
    used, scores, details = set(), [], []
    order_ok = True
    for wi, w in enumerate(want):
        best, best_j, best_d = 0.0, None, f"{w['name']}: not called"
        for j, c in enumerate(calls):
            if j in used:
                continue
            s, d = _call_score(w, c, lang, exp.get("allow_extra_args", False))
            if s > best or best_j is None and c.get("name") == w["name"]:
                best, best_j, best_d = s, j, d
        if best_j is not None:
            used.add(best_j)
            if exp.get("order") == "strict" and best_j != wi:
                order_ok = False
        scores.append(best)
        details.append(best_d)
    score = sum(scores) / len(want)
    extra_calls = len(calls) - len(used)
    if extra_calls and not exp.get("allow_extra_calls", False):
        score *= len(want) / len(calls)
        details.append(f"{extra_calls} unexpected extra call(s): {[calls[j].get('name') for j in range(len(calls)) if j not in used]}")
    if not order_ok:
        score *= 0.5
        details.append("wrong call order")
    passed = all(s == 1.0 for s in scores) and (not extra_calls or exp.get("allow_extra_calls")) and order_ok
    return _res(score, passed, "; ".join(details) + note)


# --- environment state ------------------------------------------------------

def _select(items, where, lang):
    out = []
    for it in items:
        ok = True
        for k, v in (where or {}).items():
            v = resolve(v, lang)
            if isinstance(v, dict):
                v = {kk: resolve(vv, lang) for kk, vv in v.items()}
            cur = it.get(k)
            if isinstance(v, dict) and "contains" in v:
                hay = " ".join(map(str, cur)) if isinstance(cur, list) else str(cur or "")
                ok &= contains(v["contains"], hay)
            elif isinstance(v, dict) and "regex" in v:
                ok &= matches(v["regex"], " ".join(map(str, cur)) if isinstance(cur, list) else str(cur or ""))
            elif isinstance(cur, list) and not isinstance(v, list):
                ok &= any(fold(str(x)) == fold(str(v)) for x in cur)
            else:
                ok &= (fold(str(cur)) == fold(str(v))) if isinstance(v, str) else cur == v
        if ok:
            out.append(it)
    return out


def _rule(rule, state, lang):
    coll = rule["select"]
    items = state.get(coll)
    if items is None:
        return False, f"{coll}: no such collection"
    sel = _select(items, rule.get("where"), lang)
    label = rule.get("label") or f"{coll} where {rule.get('where') or '*'}"
    if "count" in rule:
        c = rule["count"]
        ok = (len(sel) == c) if isinstance(c, int) else (c.get("min", 0) <= len(sel) <= c.get("max", 10 ** 9))
        return ok, f"{label}: count {len(sel)} (want {c})"
    if "exists" in rule:
        ok = bool(sel) == bool(rule["exists"])
        return ok, f"{label}: exists={bool(sel)} (want {rule['exists']})"
    if not sel:
        return False, f"{label}: nothing selected"
    field = rule["field"]
    vals = [it.get(field) for it in sel]
    if rule.get("union"):  # e.g. recipients across several emails
        merged = []
        for v in vals:
            merged += v if isinstance(v, list) else [v]
        vals = [merged]
    checks = {k: resolve(rule[k], lang) for k in ("eq", "contains_all", "contains_any", "regex", "set", "subset", "approx") if k in rule}
    def ok_one(v):
        txt = " ".join(map(str, v)) if isinstance(v, list) else str(v or "")
        if "eq" in checks and not ((fold(str(v)) == fold(checks["eq"])) if isinstance(checks["eq"], str) else v == checks["eq"]):
            return False
        if "contains_all" in checks and not all(contains(x, txt) for x in checks["contains_all"]):
            return False
        if "contains_any" in checks and not any(contains(x, txt) for x in checks["contains_any"]):
            return False
        if "regex" in checks and not matches(checks["regex"], txt):
            return False
        if "set" in checks and not (isinstance(v, list) and {fold(str(x)) for x in v} == {fold(str(x)) for x in checks["set"]}):
            return False
        if "subset" in checks and not (isinstance(v, list) and {fold(str(x)) for x in checks["subset"]} <= {fold(str(x)) for x in v}):
            return False
        if "approx" in checks:
            try:
                if abs(float(v) - float(checks["approx"])) > float(rule.get("tol", 0.01)):
                    return False
            except (TypeError, ValueError):
                return False
        return True
    ok = all(ok_one(v) for v in vals) if rule.get("all") else any(ok_one(v) for v in vals)
    return ok, f"{label}.{field}: {str(vals)[:120]} vs {checks}"


def score_env_state(check, rec, case, lang):
    state = rec.get("env_state")
    if state is None:
        return _res(0, False, "no environment state recorded")
    results = [_rule(r, state, lang) for r in check["expect"]]
    n = sum(ok for ok, _ in results)
    return _res(n / len(results), n == len(results),
                "; ".join(("ok " if ok else "FAIL ") + d for ok, d in results))


# --- code -------------------------------------------------------------------

def score_code_tests(check, rec, case, lang):
    code = extract_code(final_text(rec), check["entry"])
    if check["entry"] not in code:
        return _res(0, False, f"function {check['entry']!r} not found in the reply")
    out = run_python(code, check["tests"], timeout=int(check.get("timeout", 15)))
    if not out.get("loaded"):
        return _res(0, False, f"code did not load: {out.get('error')}")
    fails = [f"{t[:60]} -> {err}" for t, ok, err in out["results"] if not ok]
    frac = out["passed"] / max(out["total"], 1)
    return _res(frac, out["passed"] == out["total"],
                f"{out['passed']}/{out['total']} tests" + (f"; failed: {fails[:3]}" if fails else "")
                + ("" if out.get("network_isolated") else " [no network isolation available]"))


def score_rubric(check, rec, case, lang):
    return None  # graded by the judge (qbench grade --judge ...)


SCORERS = {
    "exact": score_exact, "numeric": score_numeric, "match": score_match, "answer": score_answer,
    "json_schema": score_json_schema, "ifeval": score_ifeval, "tool_call": score_tool_call,
    "env_state": score_env_state, "code_tests": score_code_tests, "rubric": score_rubric,
}


# Scorers that grade what the model SAID. On an empty reply they must score 0: otherwise vacuous
# rules ("no commas", "at most 40 words", not_regex) hand out credit for saying nothing.
CONTENT_SCORERS = {"exact", "numeric", "match", "answer", "json_schema", "ifeval", "code_tests", "rubric"}


def score_check(check, rec, case, lang, run_code=True):
    if rec.get("error") and not rec.get("final"):
        return _res(0, False, f"no usable answer: {rec['error'][:200]}")
    if check["scorer"] in CONTENT_SCORERS and not final_text(rec).strip():
        fr = (rec.get("final") or {}).get("finish_reason")
        return _res(0, False, "empty reply" + (" (ran out of max_tokens while reasoning)" if fr == "length" else f" (finish_reason={fr})"))
    if check["scorer"] == "code_tests" and not run_code:
        return {"score": None, "passed": None, "detail": "not executed: run was generated on another host (grade with --trust-code)"}
    return SCORERS[check["scorer"]](check, rec, case, lang)
