"""Small shared helpers: hashing, text normalisation, path scrubbing, JSONL I/O."""

import hashlib
import json
import os
import unicodedata
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Published results (graded runs, leaderboard, judge calibrations). Keep them OUT of the tool repo by pointing
# this at a results repo, e.g. QBENCH_RESULTS=~/local-llm-bench-results/quality. Scratch runs stay in ./runs.
RESULTS_DIR = os.path.abspath(os.path.expanduser(os.environ.get("QBENCH_RESULTS") or os.path.join(REPO, "results")))
RUNS_DIR = os.path.abspath(os.path.expanduser(os.environ.get("QBENCH_RUNS") or os.path.join(REPO, "runs")))
METHODOLOGY_URL = "https://github.com/michal-koniu/local-llm-quality-bench/blob/main/docs/METHODOLOGY.md"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj):
    """Stable hash of a JSON-serialisable object (sorted keys, no whitespace)."""
    return sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


SPACES = dict.fromkeys(map(ord, "\u00a0\u202f\u2007\u2009\u200a\u2002\u2003"), " ")
SPACES.update({ord("\u2019"): "'", ord("\u2018"): "'", ord("\u02bc"): "'"})  # can\u2019t -> can't


def nfc(text):
    """NFC, exotic spaces -> " ", curly apostrophes -> "'" (models write 10\u202f%, 1\u00a0234,56 zł, can\u2019t)."""
    return unicodedata.normalize("NFC", text or "").translate(SPACES)


def fold(text):
    """NFC + casefold + collapsed whitespace. Diacritics are KEPT: 'zrob' != 'zrób'."""
    return " ".join(nfc(text).casefold().split())


def seed_for(*parts):
    """Deterministic 31-bit seed from run/case/lang/sample identifiers."""
    return zlib.crc32("|".join(str(p) for p in parts).encode()) & 0x7FFFFFFF


def scrub_home(obj):
    """Replace the user's home directory with '~' everywhere in a JSON-like object."""
    home = os.path.expanduser("~")
    if not home or home == "/":
        return obj
    if isinstance(obj, str):
        return obj.replace(home, "~")
    if isinstance(obj, list):
        return [scrub_home(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_home(v) for k, v in obj.items()}
    return obj


UNPARSEABLE_MARKERS = ("does not match the expected", "produced output that", "failed to parse tool call",
                       "could not parse", "couldn't be parsed")


def error_class(rec):
    """None (clean answer) | 'max_turns' | 'unparseable' | 'transport'.
    'max_turns' and 'unparseable' are the MODEL's behaviour (it kept calling tools, or emitted output the
    server's tool-call/format parser rejected) and are scored as failures; 'transport' (connection errors,
    timeouts, other server errors) says nothing about the model, so it is retried and never scored."""
    err = rec.get("error")
    if not err:
        return None
    if err.startswith("max_turns"):
        return "max_turns"
    if any(m in err.lower() for m in UNPARSEABLE_MARKERS):
        return "unparseable"
    return "transport"


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def rel(path):
    """Path relative to the repo root when inside it, else home-scrubbed."""
    ap = os.path.abspath(path)
    if ap.startswith(REPO + os.sep):
        return os.path.relpath(ap, REPO)
    return scrub_home(ap)
