"""Frozen web: a search engine and page fetcher over a saved snapshot of shop pages.

Live prices change hourly, so a live "find the cheapest offer" test can never be
replicated. Here the web is a versioned JSON snapshot (envs/shop/<version>/pages.json,
or envs/shop/<version>/<lang>/pages.json when a snapshot has one web per language):
the right answer is known exactly and is identical on every machine.

Search is deliberately simple and deterministic: token overlap (with crude prefix
stemming, so Polish inflections match) over title + keywords + text, ties broken by URL.
"""

import json
import os
import re

from ..util import REPO, fold
from . import Env

TOKEN_RE = re.compile(r"[\w]+", re.U)
STEM = 5  # compare the first 5 characters of tokens: "słuchawki"/"słuchawek" -> "słuch"


def _tokens(text):
    return {t[:STEM] for t in TOKEN_RE.findall(fold(text)) if len(t) > 1}


def snapshot_path(version, lang=None):
    """A per-language web (envs/shop/<version>/<lang>/pages.json) wins over a shared one."""
    if lang:
        per_lang = os.path.join(REPO, "envs", "shop", version, lang, "pages.json")
        if os.path.exists(per_lang):
            return per_lang
    return os.path.join(REPO, "envs", "shop", version, "pages.json")


def load_snapshot(version, lang=None):
    path = snapshot_path(version, lang)
    with open(path, encoding="utf-8") as f:
        pages = json.load(f)
    return {p["url"]: p for p in pages}


class ShopEnv(Env):
    type = "shop"

    def __init__(self, spec, lang):
        super().__init__(spec, lang)
        self.version = spec.get("snapshot", "v1")
        self.pages = load_snapshot(self.version, lang)
        self.max_results = int(spec.get("max_results", 8))
        self.fetched = []
        self.searches = []

    def tools(self):
        return [
            {"type": "function", "function": {
                "name": "web_search",
                "description": "Search the web. Returns a list of results with url, title and a short snippet.",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string", "description": "search query"}},
                    "required": ["query"]}}},
            {"type": "function", "function": {
                "name": "fetch_page",
                "description": "Fetch the full text of a web page by its URL.",
                "parameters": {"type": "object", "properties": {
                    "url": {"type": "string", "description": "page URL from search results"}},
                    "required": ["url"]}}},
        ]

    def handlers(self):
        return {"web_search": self.web_search, "fetch_page": self.fetch_page}

    def web_search(self, query):
        q = _tokens(query)
        self.searches.append(query)
        if not q:
            return {"results": []}
        scored = []
        for url, p in self.pages.items():
            hay = _tokens(" ".join([p.get("title", ""), " ".join(p.get("keywords", [])), p.get("text", "")]))
            title = _tokens(p.get("title", "") + " " + " ".join(p.get("keywords", [])))
            hits = len(q & hay)
            if hits:
                scored.append((-(hits + 0.5 * len(q & title)), url))
        scored.sort()
        results = []
        for _, url in scored[:self.max_results]:
            p = self.pages[url]
            results.append({"url": url, "title": p.get("title", ""), "snippet": p.get("snippet", p.get("text", "")[:160])})
        return {"results": results}

    def fetch_page(self, url):
        url = url.strip()
        p = self.pages.get(url) or self.pages.get(url.rstrip("/"))
        self.fetched.append(url)
        if not p:
            return {"error": "404 Not Found", "url": url}
        return {"url": p["url"], "title": p.get("title", ""), "text": p.get("text", "")}

    def state(self):
        return {"searches": list(self.searches), "fetched": list(self.fetched)}
