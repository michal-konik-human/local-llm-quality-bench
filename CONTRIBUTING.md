# Contributing to local-llm-quality-bench

Thanks for considering it! This bench is only as good as its tasks and as useful as the number
of models it has seen, so both kinds of help matter. You don't need to be an ML researcher. A
well-written case with a clear correct answer is worth more than a clever scorer.

## Good first contributions

| Contribution | Effort | How |
|---|---|---|
| **Run the suite on your model/hardware** and share the results | 1 command + waiting | `qbench run → grade → report` (see README), then open an issue with `summary.json` + `manifest.json`, or a PR to your own results repo and a link |
| **A model profile** for a model not in `models/` | 10 min | Copy `models/_example-vllm.yaml`; every preset needs a `source` (link to the vendor's recommended settings) |
| **Review the Polish cases** (native speakers!) | 5 min per case | Read the case, check that the Polish is natural and that the expected answer accepts all correct forms, then open a PR changing `review: pending-owner` to `review: frozen` (or with fixes) |
| **A new case** | 30–60 min | See [docs/WRITING-CASES.md](docs/WRITING-CASES.md); start from a similar case in the same family |
| **Report a wrong grade** | 5 min | Open an issue with the case id, language, sample and why the verdict is wrong; a too-narrow regex is a bug |
| **Another language** (e.g. German, Czech, Ukrainian) | bigger | Open an issue first: `langs` is generic, but counting rules, register checks and the language detector need a per-language section |

## Ground rules

1. **Every check needs a `source`:** a sentence explaining why the expected answer is correct,
   precise enough that someone else would reach the same verdict.
2. **A correct answer must never fail.** When you write `regex`/`contains` checks, think about
   every legitimate way to say it (inflection, word order, units, PL/EN number formats). Run your
   case against at least one capable model and read the transcript before opening the PR.
3. **Prompt changes are new tests.** Changing what the model sees (messages, system prompt,
   tools, environment) changes the case's prompt hash, and old samples can no longer be graded
   against it. Bump `version`. Changing only checks is fine: re-grade with `qbench grade`.
4. **No live data.** Tasks must not depend on today's date, the internet or randomness. Anchor
   dates in the system prompt; put web content in a frozen environment snapshot (`envs/shop/v2/`
   for new pages, never edit `v1`).
5. **Fictional data only.** Invented people and companies, shops under the reserved `.example` domain.
6. **Engine changes come with tests.** `python3 -m unittest discover -s tests` must pass (stdlib
   only, no server needed). Keep the dependency list at PyYAML.

## Development setup

```bash
git clone https://github.com/michal-konik-human/local-llm-quality-bench.git && cd local-llm-quality-bench
python3 -m pip install -r requirements.txt     # PyYAML
python3 -m unittest discover -s tests          # 47 tests, ~3 s
python3 -m qbench validate                     # schema-check all cases and profiles
```

Keep your own results out of this repo: `export QBENCH_RESULTS=~/my-llm-results/quality` (the
default, `./results`, is git-ignored).

## Pull requests

- One topic per PR (a case, a profile, a scorer fix).
- Describe how you verified it: which model you ran it against and what the grade was.
- For scorer changes, say whether existing published grades would change (re-grading is cheap,
  but readers should know).

## Code of conduct

Be kind, assume good faith, critique the work rather than the person. Harassment of any kind
is not tolerated. Maintainers may remove comments or contributors that break this.
