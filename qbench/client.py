"""Minimal OpenAI-compatible HTTP client (stdlib only)."""

import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class HTTPError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


class Client:
    def __init__(self, base_url, api_key=None, timeout=1800, retries=2):
        self.base_url = base_url.rstrip("/")
        parts = urlsplit(self.base_url)
        self.root = f"{parts.scheme}://{parts.netloc}"
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries

    @classmethod
    def from_profile(cls, prof, timeout=1800):
        ep = prof["endpoint"]
        key = os.environ.get(ep["api_key_env"]) if ep.get("api_key_env") else None
        return cls(ep["base_url"], api_key=key, timeout=ep.get("timeout", timeout))

    def _request(self, url, body=None, timeout=None):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise HTTPError(e.code, e.read().decode("utf-8", "replace")) from None

    def chat(self, body):
        """POST /chat/completions. Retries transient failures (connection reset while
        llama-swap swaps models, 502/503 while a model loads)."""
        url = f"{self.base_url}/chat/completions"
        last = None
        for attempt in range(self.retries + 1):
            try:
                return self._request(url, body)
            except HTTPError as e:
                if e.status not in (502, 503, 504):
                    raise
                last = e
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                last = e
            time.sleep(5 * (attempt + 1))
        raise last

    def get_json(self, path, timeout=30):
        """GET a path relative to the server root (e.g. '/props'). Returns None on any error."""
        try:
            return self._request(self.root + path, timeout=timeout)
        except Exception:
            return None

    def models(self):
        return self.get_json(urlsplit(self.base_url).path + "/models") or {}
