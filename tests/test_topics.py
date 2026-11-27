"""Topics: the fixed label list, the topic pages, the search mark and the sitemap. No network."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_breakdowns as gb  # noqa: E402
import generate_html as gh  # noqa: E402
import topics  # noqa: E402

HEAD = "<!DOCTYPE html>\n<html lang=\"en\">\n<head><title>" + gh.HOME_TITLE + "</title>\n  " + \
       gh.seo_tags(gh.HOME_TITLE, gh.HOME_DESCRIPTION) + '\n  <link rel="icon" href="favicon.svg"></head>\n'


def page(directory, slug, title, date, withdrawn=False):
    summary = gb.summary_block_html([gb.WITHDRAWN_NOTE if withdrawn else f"{title} shipped a thing."])
    guide = gb.guide_block_html("What to do", "", "Check it.")
    html = gb.render_breakdown_page(title, slug, "https://a.test/x", "", summary, guide, f"Source · Press · {date}",
                                    "27 Nov 2026, 09:00")
    (directory / f"{slug}.html").write_text(html, encoding="utf-8")


def site(tmp_path):
    out = tmp_path / "site" / "breakdowns"
    out.mkdir(parents=True)
    page(out, "old", "Old story", "1 Oct 2026")
    page(out, "new", "New story", "20 Nov 2026")
    page(out, "other", "Other story", "15 Nov 2026")
    page(out, "gone", "Gone story", "25 Nov 2026", withdrawn=True)
    return tmp_path / "site"


def test_valid_label_filter_keeps_list_labels_only():
    assert topics.valid_topics(["security", "Agents"]) == ["security", "agents"]  # a slug or a name, any case
    assert topics.valid_topics(["Security", "security", "chips-hardware", "llm-apps"]) == ["security", "chips-hardware"]  # 2 at most
    assert topics.valid_topics("security") == [] and topics.valid_topics(None) == []


def test_unknown_label_is_dropped_and_a_story_with_none_still_publishes(tmp_path):
    s = site(tmp_path)
    stories = topics.live_stories(s / "breakdowns", index={})
    reply = json.dumps({"old": ["crypto", "security"], "new": ["gossip"], "other": ["agents"]})
    tags = topics.update_tags(stories, tmp_path / "topics.json", ask=lambda prompt: reply)
    assert tags == {"new": [], "old": ["security"], "other": ["agents"]}
    assert json.loads((tmp_path / "topics.json").read_text()) == tags
    topics.write_topic_pages(s, stories, tags, HEAD)
    topics.sync_story_pages(s / "breakdowns", stories, tags)
    new = (s / "breakdowns" / "new.html").read_text()
    assert '<article class="article" data-pagefind-body>' in new and topics.NAV_LABEL not in new  # published, searchable, no topic row


def test_one_call_per_run_after_the_backfill(tmp_path):
    s = site(tmp_path)
    stories = topics.live_stories(s / "breakdowns", index={})
    calls = []
    ask = lambda prompt: calls.append(prompt) or json.dumps({i["id"]: ["agents"] for i in json.loads(prompt.split("Stories:\n")[1])})
    topics.BATCH_SIZE, size = 1, topics.BATCH_SIZE
    try:
        topics.update_tags(stories, tmp_path / "t.json", ask)  # no file yet: every batch
        assert len(calls) == 3
        page(s / "breakdowns", "a", "A story", "21 Nov 2026")
        page(s / "breakdowns", "b", "B story", "22 Nov 2026")
        topics.update_tags(topics.live_stories(s / "breakdowns", index={}), tmp_path / "t.json", ask)
        assert len(calls) == 4  # one call, even with 2 batches waiting
    finally:
        topics.BATCH_SIZE = size


def test_topic_page_lists_only_its_stories_newest_first(tmp_path):
    s = site(tmp_path)
    stories = topics.live_stories(s / "breakdowns", index={})
    tags = {"old": ["security"], "new": ["security", "agents"], "other": ["agents"]}
    counts = topics.write_topic_pages(s, stories, tags, HEAD)
    assert counts["security"] == 2 and counts["agents"] == 2 and counts["llm-apps"] == 0
    html = (s / "topics" / "security" / "index.html").read_text()
    assert html.index("New story") < html.index("Old story") and "Other story" not in html
    assert '<a class="archive-title" href="../../breakdowns/new.html">New story</a>' in html and "20 Nov 2026" in html
    assert "<title>Security — Topics — Daily Edge AI</title>" in html
    assert f'<link rel="canonical" href="{gh.SITE_URL}/topics/security/">' in html and 'property="og:title"' in html
    assert 'href="../../favicon.svg"' in html and 'aria-current="page">Security</a>' in html
    index = (s / "topics" / "index.html").read_text()
    assert len({(s / "topics" / slug / "index.html").read_text().split("<title>")[1] for _, slug in topics.TOPICS}) == 8
    assert '<a class="archive-title" href="security/">Security</a>' in index and "2 stories" in index


def test_withdrawn_pages_get_no_topic_and_no_search_mark(tmp_path):
    s = site(tmp_path)
    stories = topics.live_stories(s / "breakdowns", index={})
    assert [st["id"] for st in stories] == ["new", "old", "other"]
    (tmp_path / "t.json").write_text(json.dumps({"gone": ["security"], "old": ["security"]}))
    tags = topics.update_tags(stories, tmp_path / "t.json", ask=lambda prompt: "{}")
    assert "gone" not in tags  # a withdrawn page loses its entry
    topics.write_topic_pages(s, stories, {**tags, "gone": ["security"]}, HEAD)
    topics.sync_story_pages(s / "breakdowns", stories, {**tags, "gone": ["security"]})
    for slug in ("security", ""):
        assert "Gone story" not in (s / "topics" / slug / "index.html").read_text()
    gone, old = (s / "breakdowns" / "gone.html").read_text(), (s / "breakdowns" / "old.html").read_text()
    assert "data-pagefind-body" not in gone and topics.NAV_LABEL not in gone
    assert '<article class="article" data-pagefind-body>' in old
    assert '<nav class="article-actions" aria-label="Topics of this story"><a class="text-link" href="../topics/security/">Security</a></nav>' in old
    topics.sync_story_pages(s / "breakdowns", stories, tags)
    assert (s / "breakdowns" / "old.html").read_text() == old  # a second run changes nothing
    assert gb.restyle_page(old) == old  # a page framed again keeps its topic row and its search mark
    html = (s / "topics" / "security" / "index.html").read_text()
    assert '<main class="content" id="main" data-pagefind-body>' in html


def test_sitemap_includes_topic_pages(tmp_path):
    s = site(tmp_path)
    (s / "index.html").write_text("<html></html>")
    stories = topics.live_stories(s / "breakdowns", index={})
    topics.write_topic_pages(s, stories, {}, HEAD)
    paths = [p for p, _ in gh.site_pages(str(s))]
    assert "topics/" in paths and all(f"topics/{slug}/" in paths for _, slug in topics.TOPICS)
    assert f"<loc>{gh.SITE_URL}/topics/security/</loc>" in gh.sitemap_xml(gh.site_pages(str(s)))
    assert "breakdowns/gone.html" not in paths


def test_reply_parsing():
    assert topics.parse_tags("not json", ["a"]) is None and topics.parse_tags("[]", ["a"]) is None
    assert topics.parse_tags('```json\n{"a": ["Security"], "z": ["agents"]}\n```', ["a", "b"]) == {"a": ["security"]}
