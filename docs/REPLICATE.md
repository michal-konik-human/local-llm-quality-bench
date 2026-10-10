# Replicating a result

Every published run (for the reference rig: [local-llm-bench-results/quality](https://github.com/michal-konik-human/local-llm-bench-results/tree/main/quality))
can be reproduced from what is in that run's folder and in this repository, plus the same model
weights and a server that speaks the OpenAI chat-completions API.

## Level 1: one sample, by hand (no qbench needed)

Each line of `<run>/samples.jsonl` is one graded answer. Its `request` field is the
**exact JSON body** that was POSTed, with every sampler, the seed, the tools and the messages:

```bash
RUN=path/to/<run-id>      # e.g. local-llm-bench-results/quality/<run-id>
python3 - "$RUN" tool-weather-diacritics pl 0 <<'EOF' > request.json
import json, sys
run, case, lang, sample = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
for line in open(f"{run}/samples.jsonl", encoding="utf-8"):
    r = json.loads(line)
    if (r["case"], r["lang"], r["sample"]) == (case, lang, sample) and not r.get("error"):
        print(json.dumps(r["request"], ensure_ascii=False))
EOF
curl -s http://127.0.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' -d @request.json
```

Or let qbench do it and compare:

```bash
python3 -m qbench replay <run-id> tool-weather-diacritics pl 0 --base-url http://127.0.0.1:8080/v1
```

The same `turns`, `transcript` and `env_calls` fields show the whole conversation of a multi-turn
agent sample: every tool call, and what the mock environment answered.

## Level 2: the same configuration on your machine

1. **Get the same weights.** `manifest.json → model.profile.weights` names the Hugging Face repo and
   file, and `server.llama_cpp.model_files` lists the exact files and byte sizes. Compare your file's
   sha256 with the LFS hash on its Hugging Face page.
2. **Serve them with the same settings.** `server.llama_swap.cmd` is the full llama-server command
   line used (paths shown as `~`). `server.llama_cpp.build_info` is the llama.cpp build, and
   `chat_template_sha256` tells you whether your server applies the same chat template:
   ```bash
   curl -s http://127.0.0.1:8080/props | python3 -c "import sys,json,hashlib;print(hashlib.sha256(json.load(sys.stdin)['chat_template'].encode()).hexdigest())"
   ```
3. **Use the same profile and preset.** Point `models/<name>.yaml → endpoint` at your server
   (any OpenAI-compatible server works: llama.cpp, llama-swap, vLLM, SGLang, Ollama, LM Studio) and run:
   ```bash
   python3 -m qbench run --model <name> --preset <preset> --k <k>
   python3 -m qbench grade runs/<new-run>            # same judge profiles, or --judge <yours>
   python3 -m qbench report runs/<new-run>
   python3 -m qbench compare runs/<new-run> path/to/<published-run>
   ```
4. **Check the suite is identical.** Check out the bench commit recorded in `manifest.json → bench.commit`
   (the results repo also names the tagged release). `suite.suite_sha256` must match. If only a case's
   *checks* changed since, grading still works and lists the case as "checks revised"; if its *prompt*
   changed, grading refuses (unless `--allow-changed`).

## What "reproduced" means

- **Same machine, same build, same request:** usually the same tokens. llama.cpp is deterministic for
  a single sequential request with a fixed seed and `cache_prompt: false`.
- **Different GPU, build or backend:** different tokens. Floating-point reductions differ between
  kernels and batch shapes, even with a fixed seed. What should reproduce is the
  **score within its confidence interval**. `qbench compare` tells you whether the difference
  between your run and a published one is distinguishable from zero.
- **A different judge** changes rubric scores only. The deterministic checks (most of the suite)
  are judge-independent. `grades.jsonl` shows which check produced which score.

If your numbers fall outside the published CIs on the same weights and settings, please open an
issue with your `manifest.json`: that is a finding about the serving stack, and worth knowing.
