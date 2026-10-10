"""Engine tests. Run: python3 -m unittest discover -s tests -v   (stdlib only, no server needed)"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qbench import CANARY  # noqa: E402
from qbench import adapters, cases, scorers, stats  # noqa: E402
from qbench.envs import make_env  # noqa: E402
from qbench.ifeval import run_rules  # noqa: E402
from qbench.judge import parse_verdicts, pick_judge, rubric_score  # noqa: E402
from qbench.sandbox import run_python  # noqa: E402
from qbench.textutil import detect_language, numbers, sentences  # noqa: E402


def resp(content="", tool_calls=None, reasoning=None, finish="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(tool_calls)]
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "timings": {"predicted_n": 5, "predicted_per_second": 50.0}}


def rec_from(r, tools_offered=True):
    n = adapters.normalize(r, tools_offered)
    return {"final": n, "turns": [{"tool_calls": n["tool_calls"], "tool_parse": n["tool_parse"]}],
            "all_tool_calls": n["tool_calls"], "error": None}


class TestCases(unittest.TestCase):
    def test_resolve_language_keyed_and_shared(self):
        self.assertEqual(cases.resolve({"pl": "Łódź", "en": "Lodz"}, "pl"), "Łódź")
        self.assertEqual(cases.resolve({"pl": "Łódź", "en": "Lodz"}, "en"), "Lodz")
        shared = {"name": "get_weather", "args": {"city": "x"}}
        self.assertIs(cases.resolve(shared, "pl"), shared)
        self.assertEqual(cases.resolve(["a"], "en"), ["a"])

    def test_scorer_names_match_registry(self):
        self.assertEqual(set(cases.SCORER_NAMES), set(scorers.SCORERS))

    def test_validate_catches_problems(self):
        c = cases.Case({"id": "x", "version": 1, "family": "reasoning", "item_type": "parallel",
                        "review": "pending-owner", "canary": CANARY,
                        "langs": {"pl": {"messages": [{"role": "user", "content": "?"}]}},
                        "checks": [{"scorer": "exact", "expected": "1"}]})
        errs = cases.validate(c)
        self.assertTrue(any("needs langs" in e for e in errs))
        self.assertTrue(any("source" in e for e in errs))

    def test_review_line_does_not_change_hash(self):
        a = "id: x\nreview: pending-owner\nfoo: 1\n"
        b = "id: x\nreview: frozen\nfoo: 1\n"
        self.assertEqual(cases.case_hash(a), cases.case_hash(b))
        self.assertNotEqual(cases.case_hash(a), cases.case_hash(a.replace("foo: 1", "foo: 2")))

    def test_shipped_suite_is_valid(self):
        errs = cases.validate_all(cases.load_cases())
        self.assertEqual(errs, [], "\n".join(errs))


class TestAdapters(unittest.TestCase):
    def test_native_tool_call(self):
        n = adapters.normalize(resp(tool_calls=[("get_weather", {"city": "Łódź"})]), True)
        self.assertEqual(n["tool_parse"], "native")
        self.assertEqual(n["tool_calls"][0]["arguments"], {"city": "Łódź"})

    def test_text_tool_call_fallback_hermes(self):
        n = adapters.normalize(resp('<tool_call> {"name": "get_weather", "arguments": {"city": "Kraków"}} </tool_call>'), True)
        self.assertEqual(n["tool_parse"], "fallback")
        self.assertEqual(n["tool_calls"][0]["name"], "get_weather")
        self.assertEqual(n["content"], "")

    def test_text_json_not_parsed_when_no_tools_offered(self):
        n = adapters.normalize(resp('{"name": "x", "arguments": {}}'), False)
        self.assertEqual(n["tool_calls"], [])

    def test_think_tags_stripped(self):
        c, r = adapters.split_reasoning("<think>hmm 41+1</think>\nODPOWIEDŹ: 42")
        self.assertEqual(c, "ODPOWIEDŹ: 42")
        self.assertIn("41+1", r)
        c, r = adapters.split_reasoning("reasoning without open tag</think>Answer: 7")
        self.assertEqual(c, "Answer: 7")
        c, r = adapters.split_reasoning("<think>never closed")
        self.assertEqual(c, "")

    def test_reasoning_content_field(self):
        n = adapters.normalize(resp("Answer: 3", reasoning="1+2"), False)
        self.assertEqual((n["content"], n["reasoning"]), ("Answer: 3", "1+2"))

    def test_bad_json_arguments_flagged(self):
        r = resp(tool_calls=[("f", {})])
        r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{oops"
        n = adapters.normalize(r, True)
        self.assertIsNotNone(n["tool_calls"][0]["error"])


class TestText(unittest.TestCase):
    def test_numbers_pl_and_en(self):
        self.assertIn(1234.56, numbers("cena 1 234,56 zł"))
        self.assertIn(1234.56, numbers("price $1,234.56"))
        self.assertIn(12.5, numbers("12,5 kg"))
        self.assertNotIn(-10.0, numbers("2026-10-10"))

    def test_polish_sentences_and_abbreviations(self):
        self.assertEqual(len(sentences("Kupiłem m.in. chleb, np. żytni. Potem wróciłem do domu.", "pl")), 2)

    def test_language_detection(self):
        self.assertEqual(detect_language("Dzień dobry, w załączniku przesyłam fakturę za wrzesień."), "pl")
        self.assertEqual(detect_language("Hello, please find the invoice for September attached."), "en")


class TestIfeval(unittest.TestCase):
    def test_formal_register(self):
        ok_text = "Szanowna Pani, uprzejmie informuję, że faktura jest gotowa. Czy mogę Pani w czymś pomóc?"
        bad_text = "Cześć, faktura jest gotowa. Możesz ją pobrać."
        self.assertTrue(run_rules(ok_text, [{"check": "formal_address_pl"}], "pl")[1])
        self.assertFalse(run_rules(bad_text, [{"check": "formal_address_pl"}], "pl")[1])

    def test_counts_and_language(self):
        text = "- jeden\n- dwa\n- trzy"
        frac, ok, _ = run_rules(text, [{"check": "bullet_count", "exact": 3}, {"check": "word_count", "max": 3}], "pl")
        self.assertTrue(ok)
        frac, ok, _ = run_rules("This is English text for you.", [{"check": "language"}], "pl")
        self.assertFalse(ok)

    def test_diacritics(self):
        self.assertFalse(run_rules("Zolta gesla jazn", [{"check": "has_diacritics"}], "pl")[1])
        self.assertTrue(run_rules("Żółta gęślą jaźń", [{"check": "has_diacritics"}], "pl")[1])


class TestToolScorer(unittest.TestCase):
    def check(self, expected, r, **kw):
        return scorers.score_tool_call({"expected": expected, **kw}, rec_from(r), None, "pl")

    def test_correct_call_with_diacritics(self):
        res = self.check({"calls": [{"name": "get_weather", "args": {"city": {"pl": "Łódź", "en": "Lodz"}}}]},
                         resp(tool_calls=[("get_weather", {"city": "łódź"})]))
        self.assertTrue(res["passed"], res)

    def test_ascii_city_fails_in_polish(self):
        res = self.check({"calls": [{"name": "get_weather", "args": {"city": {"one_of": ["Łódź"]}}}]},
                         resp(tool_calls=[("get_weather", {"city": "Lodz"})]))
        self.assertFalse(res["passed"])

    def test_irrelevance_and_ask(self):
        self.assertTrue(self.check({"none": True}, resp("Nie mam takiego narzędzia."))["passed"])
        self.assertFalse(self.check({"none": True}, resp(tool_calls=[("get_weather", {"city": "X"})]))["passed"])
        self.assertTrue(self.check({"ask": True}, resp("Dla jakiego miasta?"))["passed"])
        self.assertFalse(self.check({"ask": True}, resp("Sprawdzam."))["passed"])

    def test_extra_call_and_extra_args_penalised(self):
        exp = {"calls": [{"name": "a", "args": {"x": 1}}]}
        res = self.check(exp, resp(tool_calls=[("a", {"x": 1}), ("b", {})]))
        self.assertFalse(res["passed"])
        self.assertAlmostEqual(res["score"], 0.5)
        res = self.check(exp, resp(tool_calls=[("a", {"x": 1, "y": 2})]))
        self.assertFalse(res["passed"])

    def test_parallel_any_order(self):
        exp = {"calls": [{"name": "w", "args": {"c": "Gdańsk"}}, {"name": "w", "args": {"c": "Poznań"}}]}
        res = self.check(exp, resp(tool_calls=[("w", {"c": "Poznań"}), ("w", {"c": "Gdańsk"})]))
        self.assertTrue(res["passed"], res)


class TestEnvs(unittest.TestCase):
    def test_assistant_state_and_env_state_scorer(self):
        env = make_env({"type": "assistant", "state": {"calendar": [
            {"id": "ev1", "title": "Spotkanie", "start": "2026-10-08T10:00", "end": "2026-10-08T11:00", "attendees": []}]}}, "pl")
        out = json.loads(env.call("update_event", {"event_id": "ev1", "start": "2026-10-09T10:00", "end": "2026-10-09T11:00"}))
        self.assertIn("updated", out)
        self.assertIn("error", json.loads(env.call("update_event", {"event_id": "nope"})))
        self.assertIn("error", json.loads(env.call("create_event", {"title": "x", "start": "jutro", "end": "x"})))
        rec = {"env_state": env.state(), "final": {"content": "ok"}}
        check = {"expect": [{"select": "calendar", "where": {"id": "ev1"}, "field": "start", "eq": "2026-10-09T10:00"},
                            {"select": "sent", "count": 0}]}
        self.assertTrue(scorers.score_env_state(check, rec, None, "pl")["passed"])

    def test_shop_search_is_deterministic_and_inflection_tolerant(self):
        env = make_env({"type": "shop", "snapshot": "v1"}, "pl")
        a = env.web_search("słuchawki Sony WH-1000XM6")
        b = env.web_search("słuchawek sony wh-1000xm6")
        self.assertTrue(a["results"])
        self.assertEqual([r["url"] for r in a["results"]][:3], [r["url"] for r in b["results"]][:3])
        self.assertIn("error", env.fetch_page("https://nope.example/x"))


class TestScorersMisc(unittest.TestCase):
    def test_numeric_uses_answer_line(self):
        r = {"final": {"content": "Liczę: 10 + 20 = 30.\nODPOWIEDŹ: 1 234,50 zł"}, "error": None}
        self.assertTrue(scorers.score_numeric({"expected": 1234.5, "tol": 0.01}, r, None, "pl")["passed"])

    def test_answer_scorer_ignores_listing_without_choice(self):
        r = {"final": {"content": "Oferty: A 100 zł, B 90 zł.\nWYBÓR: https://b.example/x | 90 zł"}, "error": None}
        res = scorers.score_answer({"expected": {"contains_any": ["b.example/x"], "number": 90}}, r, None, "pl")
        self.assertTrue(res["passed"], res)

    def test_json_schema(self):
        r = {"final": {"content": '```json\n{"name": "Jan", "age": 30}\n```'}, "error": None}
        sch = {"type": "object", "required": ["name", "age"], "properties": {"age": {"type": "integer", "minimum": 0}},
               "additionalProperties": False}
        self.assertFalse(scorers.score_json_schema({"expected": sch}, r, None, "pl")["passed"])  # 'name' not in properties
        sch["properties"]["name"] = {"type": "string"}
        self.assertTrue(scorers.score_json_schema({"expected": sch}, r, None, "pl")["passed"])

    def test_generation_error_scores_zero(self):
        res = scorers.score_check({"scorer": "exact", "expected": "x"}, {"error": "HTTP 500", "final": None}, None, "pl")
        self.assertEqual((res["score"], res["passed"]), (0.0, False))


class TestSandbox(unittest.TestCase):
    def test_pass_fail_timeout(self):
        out = run_python("def add(a, b):\n    return a + b\n", ["assert add(1, 2) == 3", "assert add(1, 1) == 3"])
        self.assertEqual((out["passed"], out["total"]), (1, 2))
        out = run_python("while True:\n    pass\n", ["assert True"], timeout=2)
        self.assertFalse(out["loaded"])


class TestJudgeAndStats(unittest.TestCase):
    def test_parse_last_json_and_unknown(self):
        txt = 'thinking {"items": [{"id": "a", "verdict": "not_met"}]} final: {"items": [{"id": "a", "verdict": "met"}]}'
        v = parse_verdicts(txt, ["a", "b"])
        self.assertEqual(v["a"]["verdict"], "met")
        self.assertEqual(v["b"]["verdict"], "unknown")

    def test_rubric_score_with_defects(self):
        items = [{"id": "a", "weight": 2}, {"id": "b", "weight": 1}, {"id": "bad", "weight": -2, "critical": True}]
        v = {"a": {"verdict": "met"}, "b": {"verdict": "met"}, "bad": {"verdict": "not_met"}}
        self.assertEqual(rubric_score(items, v), (1.0, True))
        v["bad"] = {"verdict": "met"}
        s, ok = rubric_score(items, v)
        self.assertAlmostEqual(s, 1 / 3, places=3)
        self.assertFalse(ok)

    def test_judge_family_exclusion(self):
        class J:
            def __init__(self, n, f):
                self.name, self.family = n, f
        js = [J("ds", "deepseek"), J("oss", "gpt-oss")]
        self.assertEqual(pick_judge("deepseek", js).name, "oss")
        self.assertEqual(pick_judge("qwen", js).name, "ds")
        self.assertIsNone(pick_judge("x", [J("a", "x")]))

    def test_pass_hat_k_and_worst_at_k(self):
        self.assertAlmostEqual(stats.pass_hat_k(5, 4, 3), 4 / 10)
        self.assertEqual(stats.pass_hat_k(3, 3, 3), 1.0)
        self.assertAlmostEqual(stats.worst_at_k([1.0, 0.0, 1.0], 2), 1 / 3)
        self.assertAlmostEqual(stats.worst_at_k([0.2, 0.4, 0.6], 1), 0.4)

    def test_bootstrap_is_seeded(self):
        data = {f"c{i}": [i / 10] for i in range(10)}
        self.assertEqual(stats.bootstrap_ci(data), stats.bootstrap_ci(data))
        p, lo, hi = stats.bootstrap_ci(data)
        self.assertTrue(lo <= p <= hi)


class FakeClient:
    """Scripted OpenAI-compatible server: returns queued responses; /props 404 (not llama.cpp)."""
    base_url = "http://fake/v1"

    def __init__(self, script):
        self.script, self.bodies = list(script), []

    def chat(self, body):
        self.bodies.append(body)
        return self.script.pop(0) if self.script else resp("ODPOWIEDŹ: 42")

    def get_json(self, path, timeout=30):
        return None


class TestRunnerEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_multiturn_resume_manifest_and_grade(self):
        from qbench.grade import grade_run
        from qbench.profiles import load_profile
        from qbench.runner import run_suite
        prof = load_profile(os.path.join(os.path.dirname(__file__), "fixtures", "fake-model.yaml"))
        cs = cases.load_cases([os.path.join(os.path.dirname(__file__), "fixtures", "cases")])
        self.assertEqual(cases.validate_all(cs), [])
        # cases run in file order: fx-math (pl, en), then fx-todo (pl, en: tool call, then final answer)
        script = [resp("ODPOWIEDŹ: 42"), resp("ANSWER: 41")]
        for _ in range(2):
            script += [resp(tool_calls=[("add_todo", {"text": "Kupić mleko", "due": "2026-10-09"})]), resp("Gotowe.")]
        fc = FakeClient(script)
        rd = os.path.join(self.tmp, "run1")
        m = run_suite(prof, "recommended", cs, 1, rd, client=fc, log=lambda *a: None)
        self.assertIsNone(m["server"]["llama_cpp"])
        self.assertIn("llama_cpp", m["server"]["reasons"])
        first = fc.bodies[0]
        for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "seed", "cache_prompt"):
            self.assertIn(key, first)
        self.assertEqual(first["cache_prompt"], False)
        n_before = len(fc.bodies)
        run_suite(prof, "recommended", cs, 1, rd, client=fc, log=lambda *a: None)  # resume: nothing to do
        self.assertEqual(len(fc.bodies), n_before)
        graded, meta = grade_run(rd, judges=[], log=lambda *a: None)
        by = {(g["case"], g["lang"]): g for g in graded}
        self.assertTrue(by[("fx-todo", "pl")]["passed"])
        self.assertTrue(by[("fx-math", "pl")]["passed"])
        self.assertFalse(by[("fx-math", "en")]["passed"])


class TestScreeningRegressions(unittest.TestCase):
    """Bugs found by running the suite against a real model (gpt-oss-20b), kept as tests."""

    def test_curly_apostrophe_and_narrow_nbsp(self):
        r = {"final": {"content": "I can\u2019t book it. Rabat 10\u202f%."}, "error": None}
        res = scorers.score_match({"expected": {"regex": ["can't", "10 ?%"]}}, r, None, "en")
        self.assertTrue(res["passed"], res)

    def test_request_without_question_mark_counts_as_asking(self):
        r = rec_from(resp("Proszę podaj nazwę miasta, a sprawdzę prognozę."))
        res = scorers.score_tool_call({"expected": {"ask": True, "ask_regex": "miast"}}, r, None, "pl")
        self.assertTrue(res["passed"], res)

    def test_contact_search_is_word_based(self):
        env = make_env({"type": "assistant", "state": {"contacts": [
            {"id": "c1", "name": "Tomasz Wójcik", "email": "t.wojcik@x.pl", "company": "Kwadrat (IT)"},
            {"id": "c2", "name": "Anna Nowak", "email": "anna@x.pl", "company": "Kwadrat (HR)"}]}}, "pl")
        self.assertEqual([c["id"] for c in env.search_contacts("Tomek IT")["contacts"]], ["c1"])
        self.assertEqual(env.search_contacts("Kasia")["contacts"], [])

    def test_stub_env_lets_sequential_calls_count(self):
        env = make_env({"type": "stub", "results": {"get_weather": {"forecast": "sunny"}}}, "pl")
        out = json.loads(env.call("get_weather", {"city": "Gdańsk"}))
        self.assertEqual((out["forecast"], out["arguments"]["city"]), ("sunny", "Gdańsk"))
        calls = [{"name": "w", "arguments": {"c": "Gdańsk"}}, {"name": "w", "arguments": {"c": "Poznań"}}]
        rec = {"turns": [{"tool_calls": calls[:1], "tool_parse": "native"}, {"tool_calls": calls[1:], "tool_parse": "native"}],
               "all_tool_calls": calls, "final": {"content": ""}, "error": None}
        exp = {"calls": [{"name": "w", "args": {"c": "Gdańsk"}}, {"name": "w", "args": {"c": "Poznań"}}]}
        self.assertFalse(scorers.score_tool_call({"expected": exp}, rec, None, "pl")["passed"])
        self.assertTrue(scorers.score_tool_call({"expected": exp, "scope": "all"}, rec, None, "pl")["passed"])


class TestReviewRegressions(unittest.TestCase):
    """Findings of the independent code review, kept as tests."""

    def test_empty_reply_scores_zero_everywhere(self):
        rec = {"final": {"content": "", "tool_calls": [], "finish_reason": "length"},
               "turns": [{"tool_calls": [], "tool_parse": "none"}], "all_tool_calls": [], "error": None}
        for check in ({"scorer": "ifeval", "rules": [{"check": "no_commas"}]},
                      {"scorer": "match", "expected": {"not_regex": ["x"]}},
                      {"scorer": "tool_call", "expected": {"none": True}}):
            self.assertEqual(scorers.score_check(check, rec, None, "en")["score"], 0.0, check)

    def test_answer_requires_choice_line(self):
        r = {"final": {"content": "a.example/x 100 zł, b.example/y 90 zł"}, "error": None}
        self.assertFalse(scorers.score_answer({"expected": {"contains_any": ["b.example/y"], "number": 90}}, r, None, "pl")["passed"])

    def test_english_with_polish_names_is_english(self):
        self.assertEqual(detect_language("Kraków is a city in southern Poland, known for the Rynek Główny and Wawel."), "en")

    def test_judge_garbage_does_not_crash(self):
        for t in ('{"items": {"a": "met"}}', '{"items": ["met"]}', '{"items": null}'):
            self.assertEqual(parse_verdicts(t, ["a"])["a"]["verdict"], "unknown")

    def test_sandbox_ignores_forged_results(self):
        evil = ("import atexit, sys\natexit.register(lambda: sys.stdout.write("
                "'\\n@@RESULT@@{\"loaded\": true, \"results\": [[\"x\", true, \"\"]]}'))\ndef f(x): return 0\n")
        self.assertEqual(run_python(evil, ["assert f(1) == 1"])["passed"], 0)

    def test_string_number_is_a_type_error(self):
        r = rec_from(resp(tool_calls=[("t", {"temperature": "21.5"})]))
        self.assertFalse(scorers.score_tool_call({"expected": {"calls": [{"name": "t", "args": {"temperature": 21.5}}]}},
                                                 r, None, "pl")["passed"])

    def test_error_classes(self):
        from qbench.util import error_class
        self.assertEqual(error_class({"error": "max_turns (12) reached"}), "max_turns")
        self.assertEqual(error_class({"error": 'HTTP 500: {"message":"The model produced output that does not match the expected peg-native format"}'}), "unparseable")
        self.assertEqual(error_class({"error": "URLError: connection refused"}), "transport")
        self.assertIsNone(error_class({"error": None}))

    def test_sentences_ending_with_year_abbreviation(self):
        self.assertEqual(len(sentences("Kopernik urodził się w 1473 r. Zmarł w 1543 r. Był astronomem.", "pl")), 3)


class TestHardSuiteFeatures(unittest.TestCase):
    """Engine features added for the hard suite (cases-hard/)."""

    def _case(self, **kw):
        c = cases.Case({"id": "x", "version": 1, "family": "long_context", "item_type": "parallel", "review": "pending-owner",
                        "canary": CANARY, "langs": {"pl": {"messages": [{"role": "user", "content": "{{doc:d}} Pytanie"}]},
                                                    "en": {"messages": [{"role": "user", "content": "{{doc:d}} Question"}]}},
                        "documents": {"d": {"pl": "docs/v1/regulation-pl.md", "en": "docs/v1/regulation-en.md"}},
                        "checks": [{"scorer": "answer", "source": "s", "expected": {"number": 240}}], **kw})
        return c

    def test_documents_render_per_language_and_enter_the_prompt_hash(self):
        c = self._case()
        self.assertIn("Regulamin pracy zdalnej", cases.render(c, c.lang("pl")["messages"][0]["content"], "pl"))
        self.assertIn("Remote work regulation", cases.render(c, c.lang("en")["messages"][0]["content"], "en"))
        self.assertEqual(cases.validate(c), [])
        other = self._case(documents={"d": "docs/v1/policy-pl.md"})
        self.assertNotEqual(cases.prompt_hash(c), cases.prompt_hash(other))
        missing = self._case(documents={"d": "docs/v1/nope.md"})
        self.assertTrue(any("missing" in e for e in cases.validate(missing)))

    def test_case_max_tokens_raises_the_budget_and_the_hash_only_when_set(self):
        from qbench.runner import max_tokens_for
        self.assertEqual(max_tokens_for({"max_tokens": 16384}, {"max_tokens": 8192}), 16384)
        self.assertEqual(max_tokens_for({"max_tokens": 1024}, {"max_tokens": 8192}), 8192)  # never lowered
        self.assertEqual(max_tokens_for({}, {"max_tokens": 8192}), 8192)
        self.assertNotEqual(cases.prompt_hash(self._case()), cases.prompt_hash(self._case(max_tokens=16384)))

    def test_per_language_web_snapshot(self):
        pl = make_env({"type": "shop", "snapshot": "v2"}, "pl")
        en = make_env({"type": "shop", "snapshot": "v2"}, "en")
        self.assertIn("Zarząd Sensilo", json.loads(pl.call("fetch_page", {"url": "https://sensilo.example/zespol"}))["text"])
        self.assertIn("Sensilo management", json.loads(en.call("fetch_page", {"url": "https://sensilo.example/team"}))["text"])
        self.assertIn("error", json.loads(en.call("fetch_page", {"url": "https://sensilo.example/zespol"})))
        v1 = make_env({"type": "shop", "snapshot": "v1"}, "en")  # v1 is one shared web
        self.assertTrue(v1.pages)

    def test_language_keyed_env_validates(self):
        c = self._case(family="agentic", env={"pl": {"type": "assistant"}, "en": {"type": "assistant"}})
        self.assertEqual(cases.validate(c), [])
        bad = self._case(family="agentic", env={"pl": {"type": "assistant"}, "en": {"type": "nope"}})
        self.assertTrue(any("env.type" in e for e in cases.validate(bad)))

    def test_new_assistant_tools(self):
        env = make_env({"type": "assistant", "toolsets": ["rooms", "freebusy", "expenses", "travel", "todos"], "state": {
            "rooms": [{"id": "R1", "name": "A", "capacity": 6, "equipment": ["projector"],
                       "bookings": [{"start": "2026-10-15T12:00", "end": "2026-10-15T14:00", "title": "x", "by": "other"}]}],
            "others": {"a@x.pl": [{"start": "2026-10-15T09:00", "end": "2026-10-15T10:00"}]},
            "trains": [{"id": "T1", "from": "Warszawa Centralna", "to": "Gdańsk Główny", "date": "2026-10-20",
                        "departs": "06:20", "arrives": "09:05", "price": 89.0, "class": 2, "seats": 1}],
            "hotels": [{"id": "H1", "city": "Gdańsk", "name": "H", "price_per_night": 395.0, "refundable": True,
                        "distance_km": 1.9, "rooms_left": 1}],
            "todos": [{"id": "t1", "text": "a", "due": "2026-10-19", "done": False}]}}, "pl")
        self.assertEqual(json.loads(env.call("list_rooms", {"start": "2026-10-15T13:00", "end": "2026-10-15T15:00"}))["rooms"], [])
        self.assertIn("error", json.loads(env.call("book_room", {"room_id": "R1", "start": "2026-10-15T13:00",
                                                                 "end": "2026-10-15T15:00", "title": "t"})))
        env.call("book_room", {"room_id": "R1", "start": "2026-10-15T14:00", "end": "2026-10-15T16:00", "title": "t"})
        self.assertEqual(json.loads(env.call("get_free_busy", {"email": "a@x.pl", "date_from": "2026-10-15",
                                                               "date_to": "2026-10-15"}))["busy"][0]["end"], "2026-10-15T10:00")
        self.assertEqual(len(json.loads(env.call("search_trains", {"from": "Warszawa", "to": "Gdańsk", "date": "2026-10-20"}))["connections"]), 1)
        env.call("book_train", {"train_id": "T1"})
        self.assertIn("error", json.loads(env.call("book_train", {"train_id": "T1"})))  # sold out
        env.call("book_hotel", {"hotel_id": "H1", "check_in": "2026-10-20", "check_out": "2026-10-21"})
        self.assertIn("error", json.loads(env.call("submit_expense", {"employee": "a@x.pl", "invoice_no": "1", "amount_pln": "12",
                                                                      "category": "hotel", "status": "submitted"})))
        env.call("update_todo", {"todo_id": "t1", "due": "2026-10-26"})
        st = env.state()
        self.assertEqual([b["type"] for b in st["bookings"]], ["room", "train", "hotel"])
        self.assertEqual(st["todos"][0]["due"], "2026-10-26")

    def test_answer_set_partial_credit_and_env_approx(self):
        chk = {"expected": {"set": ["D-01", "D-05", "D-07"], "token_regex": r"D-\d{2}"}}
        exact = scorers.score_answer(chk, {"final": {"content": "ODPOWIEDŹ: D-07, D-01, D-05"}}, None, "pl")
        self.assertTrue(exact["passed"])
        part = scorers.score_answer(chk, {"final": {"content": "ODPOWIEDŹ: D-01, D-05, D-09"}}, None, "pl")
        self.assertFalse(part["passed"])
        self.assertAlmostEqual(part["score"], 0.5)  # 2 shared / 4 in the union
        state = {"expenses": [{"invoice_no": "INV-88", "amount_pln": 1322.15}]}
        ok = scorers.score_env_state({"expect": [{"select": "expenses", "field": "amount_pln", "approx": 1322.149, "tol": 0.01}]},
                                     {"env_state": state}, None, "pl")
        self.assertTrue(ok["passed"])
        off = scorers.score_env_state({"expect": [{"select": "expenses", "field": "amount_pln", "approx": 310}]},
                                      {"env_state": state}, None, "pl")
        self.assertFalse(off["passed"])

    def test_core_prompt_hashes_are_stable(self):
        """Published runs are re-graded only when the prompt hash still matches: new optional keys
        (documents, max_tokens, per-language webs) must not change the hash of a case that does not use them."""
        got = {c.id: c.prompt_sha256 for c in cases.load_cases(suite="core")}
        self.assertEqual(got["tool-weather-diacritics"], "46a7cb7b5d1153b0cc8165f6ed95907eb0e8ea19d20109d7307a1ed6aca40b51")
        self.assertEqual(got["shop-headphones-total-cost"], "417b033bc39cb954a4d65240cbd85312fec6b27c26e662a7a883cb6b5faaa272")

    def test_suites_are_separate(self):
        core = {c.id for c in cases.load_cases(suite="core")}
        hard = {c.id for c in cases.load_cases(suite="hard")}
        self.assertTrue(core and hard and not core & hard)
        self.assertEqual(cases.validate_all(cases.load_cases(suite="hard")), [])


if __name__ == "__main__":
    unittest.main()
