"""Deterministic tool environments for agentic cases.

An environment exposes OpenAI tool schemas, executes tool calls against an in-memory
state and lets the env_state scorer inspect the final state. Every sample gets a fresh
environment built from the case's seed state, so samples never influence each other.
"""

import json


class Env:
    type = "base"

    def __init__(self, spec, lang):
        self.spec = spec or {}
        self.lang = lang
        self.calls = []

    def tools(self):
        raise NotImplementedError

    def handlers(self):
        raise NotImplementedError

    def call(self, name, args):
        """Execute one tool call. Errors are returned to the model as text, like a real API."""
        fn = self.handlers().get(name)
        if fn is None:
            result = {"error": f"unknown tool '{name}'"}
        elif not isinstance(args, dict) or "__raw__" in args:
            result = {"error": "arguments must be a JSON object"}
        else:
            try:
                result = fn(**args)
            except TypeError as e:
                result = {"error": f"bad arguments: {e}"}
            except (KeyError, ValueError) as e:
                result = {"error": str(e)}
            except Exception as e:  # odd argument types (url=None, url=[...]) must not crash the run
                result = {"error": f"{type(e).__name__}: {e}"}
        text = json.dumps(result, ensure_ascii=False)
        self.calls.append({"name": name, "arguments": args, "result": json.loads(text)})  # snapshot, not a live reference
        return text

    def state(self):
        return {}


def make_env(spec, lang):
    if not spec:
        return None
    kind = spec.get("type")
    if kind == "shop":
        from .shop import ShopEnv
        return ShopEnv(spec, lang)
    if kind == "assistant":
        from .assistant import AssistantEnv
        return AssistantEnv(spec, lang)
    if kind == "stub":
        return StubEnv(spec, lang)
    raise ValueError(f"unknown env type {kind!r}")


class StubEnv(Env):
    """Executes the case's own tools with canned results, so a model whose chat template cannot emit
    parallel calls can still make several calls over several turns. The result echoes the call's
    arguments, merged with `results: {tool_name: {...}}` from the case."""
    type = "stub"

    def tools(self):
        return []

    def handlers(self):
        return {}

    def call(self, name, args):
        result = {"status": "ok", "tool": name, "arguments": args, **((self.spec.get("results") or {}).get(name) or {})}
        self.calls.append({"name": name, "arguments": args, "result": result})
        return json.dumps(result, ensure_ascii=False)
