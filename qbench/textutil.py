"""Language-aware text helpers for Polish and English answers."""

import json
import re

from .util import fold, nfc

PL_DIACRITICS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
PL_STOP = set("i w na z że się nie do to jest jak po o co a dla od ale czy przez już tak są być jego jej "
              "lub oraz może który która które ten ta tego także bardzo tylko jeszcze gdy więc też tym mam "
              "pan pani państwo proszę dzień dobry dziękuję".split())
EN_STOP = set("the and of to in is that for it with as was on be are this by not or have an at "
              "from you your which can will would there their they we our if but all please thank "
              "dear regards".split())
ABBREV = {
    "pl": ["np.", "tzw.", "m.in.", "itd.", "itp.", "ok.", "godz.", "ul.", "r.", "dr.", "prof.", "mgr.", "inż.",
           "tj.", "zob.", "wg.", "tel.", "nr.", "pt.", "św.", "al.", "pl.", "zł.", "gr.", "min.", "max.", "str.", "s."],
    "en": ["e.g.", "i.e.", "etc.", "mr.", "mrs.", "ms.", "dr.", "prof.", "vs.", "approx.", "no.", "st.", "inc.",
           "ltd.", "jr.", "sr.", "fig.", "p.", "pp."],
}
WORD_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.U)


def words(text):
    return WORD_RE.findall(nfc(text))


def detect_language(text):
    """'pl' | 'en' | 'unknown'. Stop-word + diacritics heuristic, enough to catch wrong-language replies."""
    ws = [w.casefold() for w in words(text)]
    if len(ws) < 3:
        return "unknown"
    pl = sum(w in PL_STOP for w in ws) + 2 * sum(ch in PL_DIACRITICS for ch in text)
    en = sum(w in EN_STOP for w in ws)
    if pl == en:
        return "unknown"
    return "pl" if pl > en else "en"


def diacritic_ratio(text):
    letters = [ch for ch in nfc(text) if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(ch in PL_DIACRITICS for ch in letters) / len(letters)


def sentences(text, lang="en"):
    """Count-friendly sentence split that does not break on Polish/English abbreviations."""
    t = " " + nfc(text).replace("\n", " ") + " "
    protected = t
    for ab in ABBREV.get(lang, []) + ABBREV["en" if lang == "pl" else "pl"]:
        protected = re.sub(r"(?i)(?<=[\s(])" + re.escape(ab), lambda m: m.group(0).replace(".", "\u2024"), protected)
    protected = re.sub(r"(\d)\.(\d)", "\\1\u2024\\2", protected)
    parts = re.split(r"(?<=[.!?…])\s+", protected.strip())
    return [p for p in (x.strip() for x in parts) if any(ch.isalnum() for ch in p)]


ANSWER_LABEL_RE = re.compile(
    r"^\s*(?:\*\*)?\s*(answer|final answer|odpowiedź|odpowiedz|choice|wybór|wybor)\s*(?:\*\*)?\s*[:：]\s*(?:\*\*)?(.*?)(?:\*\*)?\s*$",
    re.I | re.M)


def answer_line(text):
    """The value after the LAST 'ANSWER:' / 'ODPOWIEDŹ:' / 'CHOICE:' / 'WYBÓR:' label, else None."""
    found = ANSWER_LABEL_RE.findall(nfc(text or ""))
    return found[-1][1].strip() if found else None


NUM_RE = re.compile(r"(?<![\w.,])(?:[-−]?\d{1,3}(?:[ \u00a0\u202f.,]\d{3})+(?:[.,]\d+)?(?![\w])|[-−]?\d+(?:[.,]\d+)?)")


def parse_number(tok):
    """All plausible readings of a number token, PL or EN style:
    '1 234,56' -> [1234.56]; '1,234.56' -> [1234.56]; '12,5' -> [12.5];
    '1,234' (ambiguous: EN thousands or PL decimal) -> [1234.0, 1.234]."""
    t = tok.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2212", "-").strip().replace(" ", "")
    cands = []
    if "," in t and "." in t:
        cands.append(t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", ""))
    elif "," in t:
        head, _, tail = t.rpartition(",")
        if t.count(",") > 1:
            cands.append(t.replace(",", ""))
        elif len(tail) == 3 and 1 <= len(head.lstrip("-")) <= 3:
            cands += [t.replace(",", ""), t.replace(",", ".")]
        else:
            cands.append(t.replace(",", "."))
    elif t.count(".") > 1:
        cands.append(t.replace(".", ""))
    else:
        cands.append(t)
    out = []
    for c in cands:
        try:
            out.append(float(c))
        except ValueError:
            pass
    return out


def numbers(text):
    """Every reading of every number in the text (see parse_number)."""
    out = []
    for m in NUM_RE.finditer(nfc(text or "")):
        out += parse_number(m.group(0))
    return out


def extract_json(text):
    """Parse JSON from a reply: bare, fenced, or the first {...}/[...] block. Raises ValueError."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        i, j = t.find(open_ch), t.rfind(close_ch)
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    raise ValueError("no parseable JSON found")


def matches(pattern, text):
    """Case-insensitive regex search on NFC text (diacritics significant)."""
    return re.search(pattern, nfc(text or ""), re.I | re.U) is not None


def contains(needle, text):
    return fold(needle) in fold(text)
