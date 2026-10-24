"""Rebuild data/news_archive.json from every git version of site/news_archive.json.

Each day gets the UNION of the items in every version, since same-day reruns used to replace the day's entry.
An item that appears on several days stays on the newest one only. Newest day first, max 60 days.

Usage: python scripts/rebuild_news_archive.py [--dry-run]
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE, TARGET, MAX_DAYS = "site/news_archive.json", ROOT / "data" / "news_archive.json", 60


def key(item):
    return (item.get("id") or item.get("url") or "").strip()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def versions():
    """Archive contents per commit, newest first, plus the current data/ archive as the newest of all."""
    out = [json.loads(TARGET.read_text(encoding="utf-8"))] if TARGET.exists() else []
    for sha in git("log", "--format=%H", "--", SOURCE).split():
        try:
            out.append(json.loads(git("show", f"{sha}:{SOURCE}")))
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"[REBUILD] skipped {sha[:7]}: {e.__class__.__name__}")
    return out


def rebuild(archives):
    days = {}  # date -> {key: item}; newest version first, so its copy of an item and its order win
    for archive in archives:
        for entry in archive if isinstance(archive, list) else []:
            day = days.setdefault(entry.get("date"), {})
            for item in entry.get("news", []):
                if key(item):
                    day.setdefault(key(item), item)
    days.pop(None, None)
    placed, result = set(), []
    for date in sorted(days, reverse=True):
        news = [item for k, item in days[date].items() if k not in placed]
        placed.update(days[date])
        if news:
            result.append({"date": date, "total": len(news), "news": news})
    return result[:MAX_DAYS]


if __name__ == "__main__":
    archives = versions()
    result = rebuild(archives)
    print(f"[REBUILD] versions read: {len(archives)}, days: {len(result)}, "
          f"unique items: {sum(e['total'] for e in result)}")
    if "--dry-run" not in sys.argv:
        TARGET.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[REBUILD] wrote {TARGET.relative_to(ROOT)}")
