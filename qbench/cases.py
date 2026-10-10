"""Load and validate case files.

A case is one task written in one or two languages. Its identity is the sha256 of
the YAML file: two people holding files with the same hash are running the same test.
"""

import glob
import os
import re

import yaml

from . import CANARY
from .util import REPO, sha256_bytes

LANGS = ("pl", "en")
FAMILIES = (
    "tool_calling", "agentic", "web_shopping", "instruction_following",
    "writing", "reasoning", "coding",
    "web_research", "long_context",   # hard suite
)
ITEM_TYPES = {"parallel": {"pl", "en"}, "native-pl": {"pl"}, "native-en": {"en"}}
REVIEW_STATES = ("pending-owner", "frozen")
# Kept here (not imported from scorers) so validation has no import cycle;
# tests assert this matches scorers.SCORERS.
SCORER_NAMES = (
    "exact", "numeric", "match", "json_schema", "ifeval", "tool_call",
    "env_state", "answer", "code_tests", "rubric",
)
ENV_TYPES = ("shop", "assistant", "stub")
# A suite is a directory of cases. Scores of different suites are never mixed.
SUITES = {"core": "cases", "hard": "cases-hard"}
DOC_RE = re.compile(r"\{\{doc:([A-Za-z0-9_-]+)\}\}")
DOCS_ROOT = os.path.join(REPO, "envs")


class Case(dict):
    """A case is a plain dict (as loaded from YAML) plus `path` and `sha256`."""

    @property
    def id(self):
        return self["id"]

    def langs(self):
        return [lang for lang in LANGS if lang in self.get("langs", {})]

    def lang(self, lang):
        return self["langs"][lang]


def resolve(value, lang):
    """Language-keyed values are written {pl: X, en: Y}; anything else is shared."""
    if isinstance(value, dict) and value and set(value) <= set(LANGS):
        return value.get(lang)
    return value


REVIEW_LINE_RE = re.compile(r"^review:.*$", re.M)


def case_hash(text):
    """sha256 of the case file with the `review:` line masked, so freezing a case after a
    native-speaker review does not change its identity (the test itself is unchanged)."""
    return sha256_bytes(REVIEW_LINE_RE.sub("review: *", text).encode("utf-8"))


def doc_path(case, name, lang):
    """Absolute path of a document referenced as {{doc:name}} (paths in `documents` are relative to envs/)."""
    ref = resolve((case.get("documents") or {}).get(name), lang)
    return os.path.join(DOCS_ROOT, ref) if ref else None


def render(case, text, lang):
    """Replace {{doc:name}} placeholders with the document text."""
    def sub(m):
        p = doc_path(case, m.group(1), lang)
        if not p or not os.path.exists(p):
            raise ValueError(f"{case.get('id')}: document {m.group(1)!r} not found for {lang}")
        with open(p, encoding="utf-8") as f:
            return f.read().rstrip("\n")
    return DOC_RE.sub(sub, text or "")


def prompt_hash(case):
    """sha256 of everything the MODEL sees: per-language system + messages, the documents they include,
    tools, environment and turn limit. Checks, references and notes are excluded, so a corrected expected
    answer is a grading change (re-grade old samples) while any change to the prompt is a new test (re-run)."""
    from .util import sha256_file, sha256_json
    docs = {}
    for name in sorted(case.get("documents") or {}):
        for l in (case.get("langs") or {}):
            p = doc_path(case, name, l)
            docs[f"{name}:{l}"] = sha256_file(p) if p and os.path.exists(p) else None
    blob = {"langs": {l: {"system": (v or {}).get("system"), "messages": (v or {}).get("messages")}
                      for l, v in (case.get("langs") or {}).items()},
            "tools": case.get("tools"), "env": case.get("env"), "max_turns": case.get("max_turns")}
    # keys added after v1 of the bench go in only when used, so the hashes of older cases do not change
    if docs:
        blob["documents"] = docs
    if case.get("max_tokens"):
        blob["max_tokens"] = case["max_tokens"]
    web = {}
    for l in (case.get("langs") or {}):
        e = resolve(case.get("env"), l) or {}
        if isinstance(e, dict) and e.get("type") == "shop":
            from .envs.shop import snapshot_path
            ver = e.get("snapshot", "v1")
            p = snapshot_path(ver, l)
            if os.path.dirname(p) != os.path.join(REPO, "envs", "shop", ver):  # per-language webs (v2 onwards)
                web[l] = sha256_file(p) if os.path.exists(p) else None
    if web:
        blob["web"] = web
    return sha256_json(blob)


def load_case(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a YAML mapping")
    case = Case(data)
    case.path = os.path.abspath(path)
    case.sha256 = case_hash(text)
    case.prompt_sha256 = prompt_hash(case)
    return case


def find_case_files(paths=None, suite="core"):
    """Expand files/dirs/globs into a sorted list of case YAML files (default: the suite's directory)."""
    if not paths:
        default = os.path.join(REPO, SUITES[suite])
        paths = [default] if os.path.isdir(default) else []
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += glob.glob(os.path.join(p, "**", "*.yaml"), recursive=True)
        elif any(ch in p for ch in "*?["):
            out += glob.glob(p, recursive=True)
        else:
            out.append(p)
    return sorted(set(os.path.abspath(p) for p in out))


def load_cases(paths=None, families=None, ids=None, suite="core"):
    cases = [load_case(p) for p in find_case_files(paths, suite)]
    if families:
        cases = [c for c in cases if c.get("family") in families]
    if ids:
        cases = [c for c in cases if c.get("id") in ids]
    return cases


def validate(case):
    """Return a list of human-readable problems (empty list = valid)."""
    errs = []
    cid = case.get("id")
    where = cid or getattr(case, "path", "?")

    def err(msg):
        errs.append(f"{where}: {msg}")

    for key in ("id", "version", "family", "item_type", "review", "canary", "langs", "checks"):
        if key not in case:
            err(f"missing required key '{key}'")
    if errs:
        return errs
    if not isinstance(cid, str) or not cid or cid != cid.strip() or " " in cid:
        err("id must be a non-empty string without spaces")
    path = getattr(case, "path", None)
    if path and os.path.splitext(os.path.basename(path))[0] != cid:
        err(f"file name must equal id ({os.path.basename(path)})")
    if case["family"] not in FAMILIES:
        err(f"family '{case['family']}' not in {FAMILIES}")
    if case["item_type"] not in ITEM_TYPES:
        err(f"item_type '{case['item_type']}' not in {tuple(ITEM_TYPES)}")
    if case["review"] not in REVIEW_STATES:
        err(f"review must be one of {REVIEW_STATES}")
    if case["canary"] != CANARY:
        err("canary string missing or wrong")
    langs = case["langs"]
    if not isinstance(langs, dict) or not langs or not set(langs) <= set(LANGS):
        err(f"langs must be a mapping with keys from {LANGS}")
        return errs
    want = ITEM_TYPES.get(case["item_type"])
    if want and set(langs) != want:
        err(f"item_type {case['item_type']} needs langs {sorted(want)}, has {sorted(langs)}")
    for lang, spec in langs.items():
        msgs = (spec or {}).get("messages")
        if not isinstance(msgs, list) or not msgs:
            err(f"langs.{lang}.messages must be a non-empty list")
            continue
        for m in msgs:
            if m.get("role") not in ("user", "assistant", "system") or not isinstance(m.get("content"), str):
                err(f"langs.{lang}.messages: each message needs role user/assistant/system and string content")
        if msgs[-1].get("role") != "user":
            err(f"langs.{lang}.messages must end with a user message")
    tools = case.get("tools") or []
    for t in tools:
        fn = (t or {}).get("function", {})
        if t.get("type") != "function" or not fn.get("name") or "parameters" not in fn:
            err("each tool must be {type: function, function: {name, description, parameters}}")
    env = case.get("env")
    if env is not None:
        for lang in langs:  # an env may be shared or written per language ({pl: {...}, en: {...}})
            e = resolve(env, lang)
            if not isinstance(e, dict) or e.get("type") not in ENV_TYPES:
                err(f"env.type must be one of {ENV_TYPES} (for {lang})")
            elif e.get("type") == "shop":
                from .envs.shop import snapshot_path
                if not os.path.exists(snapshot_path(e.get("snapshot", "v1"), lang)):
                    err(f"env: web snapshot {e.get('snapshot', 'v1')!r} has no pages for {lang}")
    if case.get("max_tokens") is not None and (not isinstance(case["max_tokens"], int) or case["max_tokens"] <= 0):
        err("max_tokens must be a positive integer")
    for lang, spec in langs.items():
        for m in (spec or {}).get("messages") or []:
            for name in DOC_RE.findall(m.get("content") or ""):
                p = doc_path(case, name, lang)
                if not p or not os.path.exists(p):
                    err(f"langs.{lang}: document {name!r} is referenced but missing (documents.{name})")
    checks = case["checks"]
    if not isinstance(checks, list) or not checks:
        err("checks must be a non-empty list")
        return errs
    for i, ch in enumerate(checks):
        tag = f"checks[{i}]"
        if ch.get("scorer") not in SCORER_NAMES:
            err(f"{tag}: scorer '{ch.get('scorer')}' not in {SCORER_NAMES}")
            continue
        if not str(ch.get("source", "")).strip():
            err(f"{tag}: every check needs a 'source' (why this is the right answer)")
        w = ch.get("weight", 1)
        if not isinstance(w, (int, float)) or w <= 0:
            err(f"{tag}: weight must be a positive number")
        sc = ch["scorer"]
        if sc == "rubric":
            items = ch.get("items")
            if not isinstance(items, list) or not items:
                err(f"{tag}: rubric needs a non-empty 'items' list")
            else:
                for it in items:
                    if not it.get("id") or "weight" not in it or not resolve(it.get("criterion"), next(iter(langs))):
                        err(f"{tag}: rubric item needs id, weight and criterion")
                if not any(it.get("weight", 0) > 0 for it in items):
                    err(f"{tag}: rubric needs at least one positive-weight item")
        elif sc == "ifeval":
            for lang in langs:
                rules = resolve(ch.get("rules"), lang)
                if not isinstance(rules, list) or not rules:
                    err(f"{tag}: ifeval needs a non-empty 'rules' list (for {lang})")
                else:
                    from .ifeval import CHECKS
                    bad = [r.get("check") for r in rules if r.get("check") not in CHECKS]
                    if bad:
                        err(f"{tag}: unknown ifeval checks {bad}")
        elif sc == "code_tests":
            if not ch.get("tests") or not ch.get("entry"):
                err(f"{tag}: code_tests needs 'entry' (function name) and 'tests'")
        elif sc == "env_state":
            if not isinstance(ch.get("expect"), list) or not ch["expect"]:
                err(f"{tag}: env_state needs a non-empty 'expect' list")
            if env is None:
                err(f"{tag}: env_state requires the case to declare an env")
        elif sc in ("tool_call", "exact", "numeric", "match", "json_schema", "answer"):
            if "expected" not in ch:
                err(f"{tag}: {sc} needs 'expected'")
        for lang in langs:
            if isinstance(ch.get("expected"), dict) and set(ch["expected"]) <= set(LANGS) \
                    and ch["expected"] and lang not in ch["expected"]:
                err(f"{tag}: language-keyed expected is missing '{lang}'")
    if case["family"] in ("tool_calling", "agentic", "web_shopping") and not tools and env is None:
        err("tool/agent families need tools or an env")
    return errs


def validate_all(cases):
    errs = []
    seen = {}
    for c in cases:
        errs += validate(c)
        if c.get("id") in seen:
            errs.append(f"{c.get('id')}: duplicate id (also in {seen[c['id']]})")
        seen[c.get("id")] = getattr(c, "path", "?")
    return errs
