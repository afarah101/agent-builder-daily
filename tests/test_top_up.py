from generate_html import top_up


def test_empty_day_is_filled_from_newest_archive_days():
    archive = [{"date": "d3", "news": []}, {"date": "d2", "news": [{"url": "a"}, {"url": "b"}]},
               {"date": "d1", "news": [{"url": "b"}, {"url": "c"}]}]
    assert [i["url"] for i in top_up([], archive, "news", minimum=3)] == ["a", "b", "c"]


def test_full_day_is_left_alone():
    active = [{"url": str(n)} for n in range(8)]
    assert top_up(active, [{"news": [{"url": "x"}]}], "news") == active
