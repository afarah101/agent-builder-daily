"""Content-addressed cache for Groq chat completions, persisted as a committed JSON file.

key = sha256 of the full request (cache version, model, messages, temperature, max_tokens, reasoning settings).
Entries expire 7 days after creation. Callers save a response only after it parses (see put()).
"""
import hashlib
import json
import os
import tempfile
import time

CACHE_VERSION = 1
CACHE_FILE = os.getenv("LLM_CACHE_FILE", "cache/groq_cache.json")
TTL_SECONDS = 7 * 24 * 3600
MAX_BYTES = 5 * 1024 * 1024

_entries = None
_new = {}


def key(model, messages, temperature=None, max_tokens=None, reasoning_effort=None, reasoning_format=None):
    payload = {
        "v": CACHE_VERSION, "model": model, "messages": messages, "temperature": temperature,
        "max_tokens": max_tokens, "reasoning_effort": reasoning_effort, "reasoning_format": reasoning_format,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != CACHE_VERSION or not isinstance(data.get("entries"), dict):
            return {}
        return data["entries"]
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError) as e:
        print(f"[CACHE] Ignoring unreadable cache file {path}: {e}")
        return {}


def _fresh(entry, now):
    return now - entry.get("created_at", 0) <= TTL_SECONDS


def load(path=None):
    global _entries, _new
    _entries = _read(path or CACHE_FILE)
    _new = {}
    return _entries


def get(k, now=None):
    if _entries is None:
        load()
    entry = _entries.get(k)
    if entry and _fresh(entry, time.time() if now is None else now):
        return entry["response"]
    return None


def put(k, response, model, call_site="", now=None):
    if _entries is None:
        load()
    entry = {"response": response, "created_at": time.time() if now is None else now,
             "model": model, "call_site": call_site}
    _entries[k] = entry
    _new[k] = entry


def _serialize(entries):
    return json.dumps({"version": CACHE_VERSION, "entries": entries}, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def save(path=None, now=None):
    """Merge this run's new entries into the file on disk, prune expired ones, cap at MAX_BYTES, write atomically."""
    global _entries, _new
    path = path or CACHE_FILE
    now = time.time() if now is None else now
    merged = _read(path)
    merged.update(_new)
    merged = {k: v for k, v in merged.items() if _fresh(v, now)}
    text = _serialize(merged)
    if len(text.encode("utf-8")) > MAX_BYTES:
        sizes = {k: len(json.dumps(v, ensure_ascii=False).encode("utf-8")) + 80 for k, v in merged.items()}
        budget = MAX_BYTES - 64
        kept, total = {}, 0
        for k in sorted(merged, key=lambda k: merged[k].get("created_at", 0), reverse=True):
            if total + sizes[k] > budget:
                break
            kept[k] = merged[k]
            total += sizes[k]
        merged = kept
        text = _serialize(merged)
        while len(text.encode("utf-8")) > MAX_BYTES and merged:
            merged.pop(min(merged, key=lambda k: merged[k].get("created_at", 0)))
            text = _serialize(merged)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".groq_cache.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    _entries, _new = merged, {}
    return len(merged)
