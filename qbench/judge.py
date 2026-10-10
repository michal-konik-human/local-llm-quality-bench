"""LLM judge for `rubric` checks.

Design (see docs/METHODOLOGY.md for the sources):
  * a checklist, not a 1-10 scale: each rubric item is judged met / not_met / unknown;
  * items carry weights; negative weights mark defects (hallucination, wrong register, ...);
  * one call per (sample, rubric check); the judge reasons first, then returns JSON;
  * "unknown" is allowed so the judge never has to guess;
  * a judge never grades a model of its own family (falls back to a second judge);
  * length is explicitly not a quality signal.
"""

import json
import os
import re

import yaml

from .adapters import normalize
from .cases import resolve
from .client import Client
from .profiles import load_profile, preset as resolve_preset
from .util import REPO, seed_for, sha256_bytes

PROMPTS = {
    "en": {
        "system": (
            "You are a meticulous, impartial grader of AI assistant answers. You check each rubric item "
            "independently against the answer. You do not reward length, politeness or formatting unless "
            "a rubric item asks for it. You never assume facts that are not in the answer."),
        "user": (
            "## Task given to the assistant\n{task}\n\n"
            "## Reference notes for the grader (not shown to the assistant)\n{reference}\n\n"
            "## The assistant's answer (language of the task: {lang_name})\n<answer>\n{answer}\n</answer>\n\n"
            "## Rubric items\n{items}\n\n"
            "For each item decide:\n"
            "- QUALITY items: \"met\" if the answer satisfies it, else \"not_met\".\n"
            "- DEFECT items: \"met\" if the defect IS present in the answer, else \"not_met\".\n"
            "- \"unknown\" ONLY if it is genuinely impossible to decide from the answer.\n\n"
            "Think briefly, then end with exactly one JSON object and nothing after it:\n"
            "{{\"items\": [{{\"id\": \"<item id>\", \"verdict\": \"met|not_met|unknown\", \"why\": \"<one sentence>\"}}]}}"),
        "quality": "QUALITY", "defect": "DEFECT", "none": "(none)",
        "lang_names": {"pl": "Polish", "en": "English"},
    },
    "pl": {
        "system": (
            "Jesteś skrupulatnym, bezstronnym oceniającym odpowiedzi asystenta AI. Każdy punkt rubryki "
            "sprawdzasz niezależnie. Nie nagradzasz długości, uprzejmości ani formatowania, chyba że punkt "
            "rubryki tego wymaga. Nie zakładasz faktów, których nie ma w odpowiedzi."),
        "user": (
            "## Zadanie dla asystenta\n{task}\n\n"
            "## Notatki referencyjne dla oceniającego (asystent ich nie widział)\n{reference}\n\n"
            "## Odpowiedź asystenta (język zadania: {lang_name})\n<answer>\n{answer}\n</answer>\n\n"
            "## Punkty rubryki\n{items}\n\n"
            "Dla każdego punktu zdecyduj:\n"
            "- punkty JAKOŚĆ: \"met\", jeśli odpowiedź go spełnia, w przeciwnym razie \"not_met\".\n"
            "- punkty WADA: \"met\", jeśli wada WYSTĘPUJE w odpowiedzi, w przeciwnym razie \"not_met\".\n"
            "- \"unknown\" TYLKO wtedy, gdy naprawdę nie da się tego rozstrzygnąć na podstawie odpowiedzi.\n\n"
            "Pomyśl krótko, a na końcu podaj dokładnie jeden obiekt JSON i nic po nim:\n"
            "{{\"items\": [{{\"id\": \"<id punktu>\", \"verdict\": \"met|not_met|unknown\", \"why\": \"<jedno zdanie>\"}}]}}"),
        "quality": "JAKOŚĆ", "defect": "WADA", "none": "(brak)",
        "lang_names": {"pl": "polski", "en": "angielski"},
    },
}
PROMPT_SHA = sha256_bytes(json.dumps(PROMPTS, sort_keys=True, ensure_ascii=False).encode())
ITEMS_START_RE = re.compile(r"\{\s*\"items\"\s*:")


def load_judge_config(path=None):
    path = path or os.path.join(REPO, "judges", "default.yaml")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_path"] = path
    return cfg


class Judge:
    """One judge model (a model profile + judge sampling)."""

    def __init__(self, entry, cfg, client=None):
        # entry: "profile-name" or {"profile": name, "preset": preset-name}
        entry = {"profile": entry} if isinstance(entry, str) else dict(entry)
        self.prof = load_profile(entry["profile"])
        self.cfg = cfg
        self.name = self.prof["name"]
        self.family = self.prof["family"]
        base = resolve_preset(self.prof, entry.get("preset") or cfg.get("preset") or None)
        base["sampling"].update(cfg.get("sampling") or {})
        base["max_tokens"] = int(cfg.get("max_tokens", base["max_tokens"]))
        self.preset = base
        self.client = client or Client.from_profile(self.prof)

    def describe(self):
        return {"judge": self.name, "family": self.family, "endpoint_model": self.prof["endpoint"]["model"],
                "preset": self.preset["name"],
                "sampling": self.preset["sampling"], "max_tokens": self.preset["max_tokens"],
                "extra_body": self.preset["extra_body"], "prompt_sha256": PROMPT_SHA,
                "prompt_lang": self.cfg.get("prompt_lang", "match")}


def pick_judge(candidate_family, judges):
    """First judge whose family differs from the candidate's. judges: [Judge] in preference order."""
    for j in judges:
        if j.family != candidate_family:
            return j
    return None


def _task_text(case, lang):
    spec = case.lang(lang)
    parts = []
    if spec.get("system"):
        parts.append(f"[system]\n{spec['system']}")
    for m in spec["messages"]:
        parts.append(f"[{m['role']}]\n{m['content']}")
    return "\n\n".join(parts)


def build_prompt(case, check, lang, answer, prompt_lang):
    pl = lang if prompt_lang == "match" else prompt_lang
    P = PROMPTS[pl]
    lines = []
    for it in check["items"]:
        kind = P["defect"] if it["weight"] < 0 else P["quality"]
        crit = resolve(it["criterion"], pl) or resolve(it["criterion"], lang)
        lines.append(f"- id: {it['id']} | {kind} | {crit}")
    ref = resolve(check.get("reference"), lang) or case.lang(lang).get("reference") or P["none"]
    user = P["user"].format(task=_task_text(case, lang), reference=ref, lang_name=P["lang_names"][lang],
                            answer=answer or P["none"], items="\n".join(lines))
    return [{"role": "system", "content": P["system"]}, {"role": "user", "content": user}]


def _json_objects(text):
    """Every JSON object that starts with {"items" in the text, in order of appearance."""
    dec = json.JSONDecoder()
    out = []
    for m in ITEMS_START_RE.finditer(text or ""):
        try:
            obj, _ = dec.raw_decode(text, m.start())
            out.append(obj)
        except ValueError:
            continue
    return out


def parse_verdicts(text, item_ids):
    """Take the LAST {"items": [...]} object in the judge's reply. Missing items -> unknown."""
    verdicts = {}
    for obj in reversed(_json_objects(text)):
        for it in obj.get("items", []) if isinstance(obj, dict) else []:
            v = str(it.get("verdict", "")).strip().lower().replace(" ", "_").replace("-", "_")
            if it.get("id") in item_ids and v in ("met", "not_met", "unknown"):
                verdicts[it["id"]] = {"verdict": v, "why": str(it.get("why", ""))[:300]}
        if verdicts:
            break
    for i in item_ids:
        verdicts.setdefault(i, {"verdict": "unknown", "why": "judge gave no parseable verdict"})
    return verdicts


def rubric_score(items, verdicts, threshold=0.8):
    pos = sum(it["weight"] for it in items if it["weight"] > 0 and verdicts[it["id"]]["verdict"] != "unknown")
    if pos <= 0:
        return None, False
    got = sum(it["weight"] for it in items if it["weight"] > 0 and verdicts[it["id"]]["verdict"] == "met")
    pen = sum(-it["weight"] for it in items if it["weight"] < 0 and verdicts[it["id"]]["verdict"] == "met")
    score = max(0.0, min(1.0, (got - pen) / pos))
    critical = any(it.get("critical") and verdicts[it["id"]]["verdict"] == ("met" if it["weight"] < 0 else "not_met")
                   for it in items)
    return round(score, 4), (score >= threshold and not critical)


def grade(judge, case, check, check_idx, rec, lang):
    answer = (rec.get("final") or {}).get("content") or ""
    msgs = build_prompt(case, check, lang, answer, judge.cfg.get("prompt_lang", "match"))
    body = {"model": judge.prof["endpoint"]["model"], "messages": msgs, "stream": False,
            "max_tokens": judge.preset["max_tokens"], "cache_prompt": False,
            "seed": seed_for("judge", rec["run_id"], rec["case"], lang, rec["sample"], check_idx)}
    body.update(judge.preset["sampling"])
    body.update(judge.preset["extra_body"])
    try:
        resp = judge.client.chat(body)
    except Exception as e:  # judge failures must not kill a long grading run
        return {"score": None, "passed": False, "detail": f"judge error: {type(e).__name__}: {e}"[:300],
                "judge": judge.name, "verdicts": None}
    norm = normalize(resp)
    text = norm["content"] or ""
    ids = [it["id"] for it in check["items"]]
    verdicts = parse_verdicts(text, ids)
    if all(v["verdict"] == "unknown" for v in verdicts.values()) and norm["reasoning"]:
        verdicts = parse_verdicts(norm["reasoning"] + "\n" + text, ids)
    score, passed = rubric_score(check["items"], verdicts, float(check.get("pass_threshold", 0.8)))
    met = [i for i, v in verdicts.items() if v["verdict"] == "met"]
    unk = [i for i, v in verdicts.items() if v["verdict"] == "unknown"]
    detail = f"met: {met}" + (f"; unknown: {unk}" if unk else "")
    return {"score": score, "passed": passed, "detail": detail, "judge": judge.name, "verdicts": verdicts,
            "judge_usage": resp.get("usage")}
