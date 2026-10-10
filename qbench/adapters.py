"""Normalise chat-completion responses from different servers and model families.

Different stacks return the same thing in different shapes:
  * reasoning in `message.reasoning_content` (llama.cpp, vLLM), `message.reasoning`
    (some proxies), or inline as <think>...</think> in `content` (raw Qwen3/DeepSeek output);
  * tool calls as native `message.tool_calls`, or as text when the server's template
    parser does not recognise the model's format (e.g. Hermes `<tool_call>{...}</tool_call>`).
Scorers only ever see the normalised form, so a model is not punished for a server quirk,
but every fallback is flagged in the record (`tool_parse: "fallback"`) so it stays visible.
"""

import json
import re

THINK_RE = re.compile(r"<think>(.*?)</think>", re.S)
TOOL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)
HARMONY_FINAL_RE = re.compile(r"<\|channel\|>final<\|message\|>(.*?)(?:<\|end\|>|<\|return\|>|$)", re.S)


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    try:
        val = json.loads(raw)
        if isinstance(val, dict):
            return val, None
        return {"__raw__": raw}, "arguments are not a JSON object"
    except (TypeError, ValueError):
        return {"__raw__": raw}, "arguments are not valid JSON"


def split_reasoning(content, reasoning=None):
    """Return (final_content, reasoning_text)."""
    content = content or ""
    parts = [reasoning] if reasoning else []
    m = HARMONY_FINAL_RE.search(content)
    if m:  # raw gpt-oss harmony output leaked through an unparsed template
        parts.append(content[:m.start()])
        content = m.group(1)
    found = THINK_RE.findall(content)
    if found:
        parts += found
        content = THINK_RE.sub("", content)
    if "</think>" in content:  # opening tag was in the prompt (Qwen3/DeepSeek templates)
        head, _, content = content.rpartition("</think>")
        parts.append(head)
    elif content.lstrip().startswith("<think>"):  # never closed: ran out of tokens while thinking
        parts.append(content.lstrip()[len("<think>"):])
        content = ""
    return content.strip(), "\n".join(p.strip() for p in parts if p and p.strip())


def _text_tool_calls(content):
    calls = []
    for blob in TOOL_TAG_RE.findall(content):
        try:
            obj = json.loads(blob)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            calls.append(obj)
    if not calls:
        text = content.strip()
        m = FENCE_RE.match(text)
        text = m.group(1) if m else text
        if text.startswith("{") or text.startswith("["):
            try:
                obj = json.loads(text)
            except ValueError:
                obj = None
            items = obj if isinstance(obj, list) else [obj]
            if obj and all(isinstance(o, dict) and o.get("name") and
                           ("arguments" in o or "parameters" in o) for o in items):
                calls = items
    out = []
    for c in calls:
        args, err = _parse_args(c.get("arguments", c.get("parameters")))
        out.append({"id": None, "name": c["name"], "arguments": args, "error": err})
    return out


def normalize(response, tools_offered=False):
    """-> {content, reasoning, tool_calls[{id,name,arguments,error}], tool_parse, finish_reason, raw_message}"""
    choice = (response.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content, reasoning = split_reasoning(msg.get("content"),
                                         msg.get("reasoning_content") or msg.get("reasoning"))
    calls, parse = [], "none"
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args, err = _parse_args(fn.get("arguments"))
        calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args, "error": err})
    if calls:
        parse = "native"
    elif tools_offered and content:
        calls = _text_tool_calls(content)
        if calls:
            parse = "fallback"
            content = TOOL_TAG_RE.sub("", content).strip()
            if content.startswith("{") or content.startswith("[") or content.startswith("```"):
                content = ""
    return {
        "content": content,
        "reasoning": reasoning,
        "tool_calls": calls,
        "tool_parse": parse,
        "finish_reason": choice.get("finish_reason"),
        "raw_message": msg,
    }
