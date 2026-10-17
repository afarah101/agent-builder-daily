import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_cache

MSG = [{"role": "user", "content": "Score this paper."}]
BASE = dict(model="openai/gpt-oss-20b", messages=MSG, temperature=None, max_tokens=400,
            reasoning_effort="low", reasoning_format="hidden")
T0 = 1_760_000_000.0


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "groq_cache.json"
    monkeypatch.setattr(llm_cache, "CACHE_FILE", str(path))
    llm_cache.load()
    return path


def test_same_request_hits_and_returns_same_text(cache_file):
    k = llm_cache.key(**BASE)
    assert llm_cache.get(k, now=T0) is None
    llm_cache.put(k, '{"score": 7}', BASE["model"], now=T0)
    assert llm_cache.get(llm_cache.key(**BASE), now=T0 + 60) == '{"score": 7}'


@pytest.mark.parametrize("field,value", [
    ("model", "openai/gpt-oss-120b"),
    ("messages", [{"role": "user", "content": "Score this paper!"}]),
    ("temperature", 0.3),
    ("max_tokens", 401),
    ("reasoning_effort", "high"),
])
def test_any_request_change_is_a_miss(cache_file, field, value):
    llm_cache.put(llm_cache.key(**BASE), "cached", BASE["model"], now=T0)
    changed = {**BASE, field: value}
    assert llm_cache.key(**changed) != llm_cache.key(**BASE)
    assert llm_cache.get(llm_cache.key(**changed), now=T0) is None


def test_entry_expires_after_7_days(cache_file):
    k = llm_cache.key(**BASE)
    llm_cache.put(k, "cached", BASE["model"], now=T0)
    assert llm_cache.get(k, now=T0 + 7 * 86400) == "cached"
    assert llm_cache.get(k, now=T0 + 7 * 86400 + 1) is None


def test_save_prunes_expired_and_round_trips(cache_file):
    old, new = llm_cache.key(**BASE), llm_cache.key(**{**BASE, "max_tokens": 1})
    llm_cache.put(old, "old", BASE["model"], now=T0)
    llm_cache.put(new, "new", BASE["model"], now=T0 + 8 * 86400)
    assert llm_cache.save(now=T0 + 8 * 86400) == 1
    llm_cache.load()
    assert llm_cache.get(new, now=T0 + 8 * 86400) == "new"
    assert llm_cache.get(old, now=T0 + 8 * 86400) is None


def test_save_is_atomic_sorted_and_leaves_no_temp_files(cache_file):
    for i in range(5):
        llm_cache.put(llm_cache.key(**{**BASE, "max_tokens": i}), f"r{i}", BASE["model"], now=T0)
    llm_cache.save(now=T0)
    text = cache_file.read_text()
    data = json.loads(text)
    assert text == json.dumps(data, sort_keys=True, indent=1, ensure_ascii=False) + "\n"
    assert list(data["entries"]) == sorted(data["entries"])
    assert [p.name for p in cache_file.parent.iterdir()] == ["groq_cache.json"]


def test_failed_write_keeps_old_file(cache_file, monkeypatch):
    llm_cache.put(llm_cache.key(**BASE), "first", BASE["model"], now=T0)
    llm_cache.save(now=T0)
    before = cache_file.read_text()
    llm_cache.put(llm_cache.key(**{**BASE, "max_tokens": 9}), "second", BASE["model"], now=T0)
    monkeypatch.setattr(llm_cache.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        llm_cache.save(now=T0)
    assert cache_file.read_text() == before
    assert [p.name for p in cache_file.parent.iterdir()] == ["groq_cache.json"]


def test_save_merges_entries_written_by_another_run(cache_file):
    llm_cache.put(llm_cache.key(**BASE), "run A", BASE["model"], now=T0)
    llm_cache.save(now=T0)
    llm_cache.load()
    other = llm_cache.key(**{**BASE, "max_tokens": 2})
    llm_cache.put(other, "run B", BASE["model"], now=T0 + 10)
    llm_cache.save(now=T0 + 10)
    llm_cache.load()
    assert llm_cache.get(llm_cache.key(**BASE), now=T0 + 10) == "run A"
    assert llm_cache.get(other, now=T0 + 10) == "run B"


def test_file_capped_at_5mb_keeping_newest(cache_file):
    big = "x" * 100_000
    for i in range(80):
        llm_cache.put(llm_cache.key(**{**BASE, "max_tokens": i}), big, BASE["model"], now=T0 + i)
    llm_cache.save(now=T0 + 80)
    assert cache_file.stat().st_size <= llm_cache.MAX_BYTES
    llm_cache.load()
    assert llm_cache.get(llm_cache.key(**{**BASE, "max_tokens": 79}), now=T0 + 80) == big
    assert llm_cache.get(llm_cache.key(**{**BASE, "max_tokens": 0}), now=T0 + 80) is None


@pytest.mark.parametrize("content", ["{not json", '{"version": 1, "entries": []}', '["a list"]', ""])
def test_corrupt_or_missing_file_loads_empty(cache_file, content):
    cache_file.write_text(content)
    assert llm_cache.load() == {}
    os.remove(cache_file)
    assert llm_cache.load() == {}


def test_version_bump_ignores_old_file(cache_file, monkeypatch):
    llm_cache.put(llm_cache.key(**BASE), "v1", BASE["model"], now=T0)
    llm_cache.save(now=T0)
    monkeypatch.setattr(llm_cache, "CACHE_VERSION", 2)
    assert llm_cache.load() == {}
