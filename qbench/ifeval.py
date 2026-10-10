"""Verifiable instruction checks (IFEval-style), written to work in Polish and English.

Each check takes (text, params, lang) and returns (passed: bool, detail: str).
Counting follows the answer's language: Polish abbreviations such as "np." or "m.in."
do not end a sentence, and diacritics are never stripped before matching.
"""

import re

from .cases import resolve
from .textutil import (detect_language, diacritic_ratio, extract_json, matches, sentences,
                       words)
from .util import fold, nfc


def _range(n, p, what):
    lo, hi, ex = p.get("min"), p.get("max"), p.get("exact")
    ok = (ex is None or n == ex) and (lo is None or n >= lo) and (hi is None or n <= hi)
    bounds = f"exact {ex}" if ex is not None else f"{lo if lo is not None else '-'}..{hi if hi is not None else '-'}"
    return ok, f"{what}={n} (want {bounds})"


def word_count(text, p, lang):
    return _range(len(words(text)), p, "words")


def sentence_count(text, p, lang):
    return _range(len(sentences(text, lang)), p, "sentences")


BULLET_RE = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+\S", re.M)


def bullet_count(text, p, lang):
    return _range(len(BULLET_RE.findall(text)), p, "bullets")


def paragraph_count(text, p, lang):
    paras = [x for x in re.split(r"\n\s*\n", text.strip()) if x.strip()]
    return _range(len(paras), p, "paragraphs")


def char_count(text, p, lang):
    return _range(len(nfc(text)), p, "chars")


def language(text, p, lang):
    want = p.get("lang", lang)
    got = detect_language(text)
    return got == want, f"detected {got}, want {want}"


def has_diacritics(text, p, lang):
    """Polish text without ogonki is a defect (quantisation and ASCII drift cause it)."""
    r = diacritic_ratio(text)
    need = p.get("min_ratio", 0.02)
    return r >= need, f"diacritic ratio {r:.3f} (want >= {need})"


def forbidden_words(text, p, lang):
    words_ = resolve(p["words"], lang)
    hit = [w for w in words_ if matches(r"(?<!\w)" + w, text)]
    return not hit, f"forbidden present: {hit}" if hit else "none present"


def required_patterns(text, p, lang):
    pats = resolve(p["patterns"], lang)
    miss = [w for w in pats if not matches(w, text)]
    return not miss, f"missing: {miss}" if miss else "all present"


def regex_absent(text, p, lang):
    pats = resolve(p["patterns"], lang)
    hit = [w for w in pats if matches(w, text)]
    return not hit, f"present: {hit}" if hit else "none present"


def starts_with(text, p, lang):
    want = resolve(p["text"], lang)
    return fold(text).startswith(fold(want)), f"starts with {want!r}?"


def ends_with(text, p, lang):
    want = resolve(p["text"], lang)
    return fold(text).rstrip().endswith(fold(want)), f"ends with {want!r}?"


def json_valid(text, p, lang):
    try:
        obj = extract_json(text)
    except ValueError as e:
        return False, str(e)
    if p.get("bare") and text.strip()[:1] not in "{[":
        return False, "JSON present but not bare (prose or code fence around it)"
    keys = p.get("keys")
    if keys:
        if not isinstance(obj, dict):
            return False, "JSON is not an object"
        miss = [k for k in keys if k not in obj]
        if miss:
            return False, f"missing keys {miss}"
    return True, "valid JSON"


def regex_count(text, p, lang):
    n = len(re.findall(resolve(p["pattern"], lang), nfc(text), re.I | re.M))
    return _range(n, p, f"matches of /{resolve(p['pattern'], lang)}/")


def no_commas(text, p, lang):
    return "," not in text, "no commas" if "," not in text else f"{text.count(',')} commas"


def all_lowercase(text, p, lang):
    letters = [c for c in text if c.isalpha()]
    return all(c.islower() for c in letters), "all lowercase?"


def wrapped_in_quotes(text, p, lang):
    t = text.strip()
    ok = len(t) >= 2 and t[0] in "\"„“”«" and t[-1] in "\"”“»"
    return ok, "wrapped in quotes?"


FORMAL_RE = r"\b(Pan|Pani|Pana|Panu|Panią|Panem|Państwo|Państwa|Państwu|Państwem)\b"
INFORMAL_RE = (r"(?<!\w)(ty|ciebie|tobie|cię|twój|twoja|twoje|twojego|twojej|twoim|twoich|"
               r"możesz|chcesz|masz|jesteś|wiesz|potrzebujesz|zrobiłeś|zrobiłaś|daj|sprawdź|napisz mi|"
               r"pamiętaj|zobacz|kliknij|wpisz)(?!\w)")


def formal_address_pl(text, p, lang):
    """Polish formal register: addresses the reader as Pan/Pani/Państwo, no 'ty' forms."""
    formal = re.search(FORMAL_RE, nfc(text)) is not None
    informal = re.findall(INFORMAL_RE, nfc(text), re.I)
    ok = formal and not informal
    return ok, f"formal forms: {formal}, informal forms: {sorted(set(i.lower() for i in informal))}"


def informal_address_pl(text, p, lang):
    """Polish informal register: no Pan/Pani/Państwo forms."""
    formal = re.findall(FORMAL_RE, nfc(text))
    return not formal, f"formal forms found: {sorted(set(formal))}" if formal else "no formal forms"


def placeholder_absent(text, p, lang):
    hit = re.findall(r"\[[A-ZĄĆĘŁŃÓŚŹŻ][^\]]{0,40}\]|<[A-Z][A-Z_ ]{2,30}>|\{\{.*?\}\}", text)
    return not hit, f"placeholders: {hit[:5]}" if hit else "no placeholders"


CHECKS = {f.__name__: f for f in (
    word_count, sentence_count, bullet_count, paragraph_count, char_count, language, has_diacritics,
    forbidden_words, required_patterns, regex_absent, regex_count, starts_with, ends_with, json_valid, no_commas,
    all_lowercase, wrapped_in_quotes, formal_address_pl, informal_address_pl, placeholder_absent)}


def run_rules(text, rules, lang):
    """-> (fraction passed, all passed, [details])."""
    results = []
    for r in rules:
        name = r.get("check")
        fn = CHECKS.get(name)
        if fn is None:
            results.append((False, f"{name}: unknown check"))
            continue
        ok, detail = fn(text or "", r, lang)
        results.append((ok, f"{name}: {'ok' if ok else 'FAIL'} ({detail})"))
    n_ok = sum(ok for ok, _ in results)
    return n_ok / len(results), n_ok == len(results), [d for _, d in results]
