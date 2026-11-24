"""header/: Groq words with fallback, each gate failing on purpose, never blocking the send, the inline CID image."""
import json
import struct
import sys
import zlib
from datetime import datetime, timedelta
from email import message_from_bytes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import send_daily_email as de  # noqa: E402
from header import gates  # noqa: E402
from header import render_header as rh  # noqa: E402

TOP = {"title": "Stalwart is now a drop-in replacement for Microsoft Exchange", "category": "Product Launch",
       "summary": "An open-source mail server.", "url": "https://example.com/a", "source": "Example"}
GOOD = [{"slot": "kicker", "color": "rgb(148, 163, 184)", "bg": "rgb(15, 17, 23)", "lines": 1},
        {"slot": "title", "color": "rgb(241, 245, 249)", "bg": "rgb(15, 17, 23)", "lines": 2, "scroll": 1040, "client": 1040},
        {"slot": "date", "color": "rgb(148, 163, 184)", "bg": "rgb(15, 17, 23)", "lines": 1},
        {"slot": "logo", "color": "rgb(0, 255, 136)", "bg": "rgb(15, 17, 23)", "lines": 1}]


def png(w=1200, h=630, pad=0):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return gates._PNG_SIG + chunk + b"\0" * pad


def good_render(page):
    return png(), GOOD


# ---------- words ----------

def test_cut_stops_at_a_word_boundary():
    assert rh.cut("Missed billing costs two hundred thousand", 20) == "Missed billing costs"
    assert rh.cut("a" * 30, 10) == "a" * 10
    assert rh.cut("short", 60) == "short"
    assert rh.cut("Amazon buys Decart's tech for $7.5B as it sells chips", 40) == "Amazon buys Decart's tech for $7.5B"


def test_words_uses_groq_json():
    reply = json.dumps({"kicker": "Email servers", "title": "Stalwart replaces Exchange"})
    assert rh.words(TOP, lambda p: reply) == ("Email servers", "Stalwart replaces Exchange", "groq")


@pytest.mark.parametrize("complete", [
    lambda p: "not json",
    lambda p: json.dumps({"kicker": "x" * 25, "title": "ok"}),
    lambda p: json.dumps({"kicker": "ok", "title": "x" * 61}),
    lambda p: json.dumps({"title": "no kicker"}),
    lambda p: (_ for _ in ()).throw(RuntimeError("groq down")),
])
def test_words_falls_back_to_the_headline(complete):
    kicker, title, source = rh.words({**TOP, "title": TOP["title"] + " servers today"}, complete)
    assert source == "fallback" and kicker == "Product Launch"
    assert title == "Stalwart is now a drop-in replacement for Microsoft Exchange"  # 60 chars, cut at a space


# ---------- each gate fails on purpose ----------

def test_title_gate():
    assert gates.title_gate("x" * 60) == []
    assert gates.title_gate("x" * 61) and gates.title_gate(" ")


def test_lines_gate():
    assert gates.lines_gate(GOOD) == []
    assert gates.lines_gate([{**GOOD[1], "lines": 3}]) == ["title wraps to 3 lines (max 2)"]
    assert gates.lines_gate([{**GOOD[1], "lines": None}]) == ["title line count unreadable (None)"]
    assert gates.lines_gate([{**GOOD[1], "lines": float("nan")}]) == ["title line count unreadable (nan)"]


def test_colour_gate_passes_the_filled_template_and_fails_off_brand():
    page = rh.fill("Email", "A title", "Tue 24 Nov 2026")
    assert gates.colour_gate(page) == []
    assert gates.colour_gate(rh.fill("Lists", "The top #100 AI tools", "d")) == []  # a '#' in the text is not a colour
    assert gates.colour_gate(page.replace("#00ff88", "#ff0000")) == ["non-brand colour #ff0000"]
    assert gates.colour_gate(page + "<p style='color:rgb(1,2,3)'>")


def test_colour_gate_scans_css_only_and_fails_named_colours():
    assert gates.colour_gate(rh.fill("Red alert", "White House bans #ff0000 tan lines", "d")) == []  # text, not CSS
    page = rh.fill("Email", "A title", "d")
    assert gates.colour_gate(page.replace("color: var(--text);", "color: red;")) == ["non-brand colour keyword red"]
    assert gates.colour_gate(page + "<p style=\"background:White\">") == ["non-brand colour keyword white"]
    assert gates.colour_gate(page + "<p style='white-space:nowrap' class='red'>") == []  # a property, a class
    assert gates.colour_gate(page + "<style>/* red */ p{border-color:transparent}</style>") == [
        "non-brand colour keyword transparent"]
    assert len(gates.NAMED) == 150  # 148 CSS named colours + transparent + currentcolor


def test_width_gate():
    assert gates.width_gate(GOOD) == []
    assert gates.width_gate([{**GOOD[1], "scroll": 1500}]) == ["title overflows its box (1500px in 1040px)"]
    assert gates.width_gate([{**GOOD[1], "scroll": None}]) == ["title width unreadable (None, 1040)"]


def chromium(fn):
    """Run fn(page) in headless Chromium; skip where Playwright or its browser is not installed."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                return fn(browser.new_page())
            finally:
                browser.close()
    except Exception as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip(f"no headless Chromium: {e}")
        raise


def test_fill_text_renders_as_typed():
    page_html = rh.fill("It's", "OpenAI's #100 AT&T", "Tue 24 Nov 2026")

    def texts(page):
        page.set_content(page_html)
        return page.evaluate("() => Object.fromEntries([...document.querySelectorAll('[data-slot]')]"
                             ".map(el => [el.dataset.slot, el.textContent]))")
    assert chromium(texts) == {"kicker": "It's", "title": "OpenAI's #100 AT&T", "date": "Tue 24 Nov 2026",
                               "logo": "Scout Daily"}
    assert gates.check_html(page_html, "OpenAI's #100 AT&T") == []


def test_real_render_wraps_a_long_word_and_the_width_gate_catches_overflow():
    page_html = rh.fill("Test", "W" * 60, "Tue 24 Nov 2026")
    try:
        _, fixed = rh.render(page_html)
        _, broken = rh.render(page_html.replace(" overflow-wrap: anywhere;", ""))
    except Exception as e:
        pytest.skip(f"no headless Chromium: {e}")
    assert gates.width_gate(fixed) == []
    assert gates.width_gate(broken)[0].startswith("title overflows its box")


def test_contrast_gate():
    assert round(gates.contrast("#000000", "#ffffff"), 1) == 21.0
    assert gates.contrast_gate(GOOD) == []
    low = [{"slot": "title", "color": "#94a3b8", "bg": "#f1f5f9", "lines": 1}]
    assert gates.contrast_gate(low)[0].startswith("title contrast 2.")


def test_png_gate():
    assert gates.png_gate(png()) == []
    assert gates.png_gate(png(1200, 627)) == ["PNG is 1200x627 (need 1200x630)"]
    assert gates.png_gate(png(pad=300 * 1024))[0].startswith("PNG is ")
    assert gates.png_gate(b"GIF89a" + b"\0" * 30) == ["output is not a PNG"]


# ---------- email_header never raises ----------

@pytest.mark.parametrize("render_fn, reason", [
    (lambda page: (png(), [{**GOOD[1], "lines": 3}]), "title wraps to 3 lines"),
    (lambda page: (png(800, 630), GOOD), "PNG is 800x630"),
    (lambda page: (png(), [{**GOOD[1], "color": "#22263a"}]), "title contrast"),
    (lambda page: (_ for _ in ()).throw(RuntimeError("no browser")), "no browser"),
])
def test_email_header_returns_none_with_the_reason(render_fn, reason, capsys):
    assert rh.email_header(TOP, "Tue 24 Nov 2026", lambda p: "{}", render_fn) is None
    assert reason in capsys.readouterr().out


def test_email_header_skips_the_render_when_a_colour_is_off_brand(monkeypatch, capsys):
    monkeypatch.setattr(rh, "fill", lambda k, t, d: "<p style='color:#123456'>")
    calls = []
    assert rh.email_header(TOP, "d", lambda p: "{}", lambda page: calls.append(page)) is None
    assert calls == [] and "non-brand colour #123456" in capsys.readouterr().out


def test_email_header_ok():
    assert rh.email_header(TOP, "Tue 24 Nov 2026", lambda p: "{}", good_render) == png()


def test_real_render_passes_every_gate():
    """End to end in headless Chromium; skipped where Playwright or its browser is not installed."""
    pytest.importorskip("playwright")
    try:
        shot, measured = rh.render(rh.fill("Email servers", "Stalwart is now a drop-in replacement for Exchange", "Tue 24 Nov 2026"))
    except Exception as e:
        pytest.skip(f"no headless Chromium: {e}")
    assert gates.check_render(measured, shot) == []


# ---------- the email ----------

NOW = datetime(2026, 11, 24, 11, 0, tzinfo=de.LONDON)
DATA = {"news": [TOP], "papers": [], "cs_days": [], "creators": [], "newest": NOW - timedelta(hours=1), "reason": None}


def test_html_has_one_cid_image_only_with_a_header():
    assert de.build_email(DATA, NOW, header=True)[1].count("cid:") == 1
    assert "cid:" not in de.build_email(DATA, NOW)[1]


def test_message_carries_the_inline_png():
    msg = message_from_bytes(de.build_message("s", "<img src='cid:scout-header'>", "t", "a@x", "b@x", png()).as_bytes())
    assert msg.get_content_type() == "multipart/related" and msg.get_param("type") == "multipart/alternative"
    alt, img = msg.get_payload()
    assert alt.get_content_type() == "multipart/alternative"
    assert img.get_content_type() == "image/png" and img["Content-ID"] == "<scout-header>"
    assert img.get_payload(decode=True) == png()
    assert de.build_message("s", "<p>", "t", "a@x", "b@x").get_content_type() == "multipart/alternative"


@pytest.mark.parametrize("header_png", [png(), None])
def test_main_sends_with_or_without_the_header(monkeypatch, tmp_path, header_png):
    monkeypatch.setattr(de, "LAST_SENT", str(tmp_path / "last_sent.txt"))
    monkeypatch.setattr(de, "load_data", lambda: DATA)
    sent = []

    def fake_send(subject, html_body, text_body, **kw):
        sent.append((html_body, kw))
        return "ok"

    assert de.main(["--force"], now=NOW, sleep=lambda s: None, send=fake_send, header=lambda top, date: header_png) == "sent"
    html_body, kw = sent[0]
    assert html_body.count("cid:") == (1 if header_png else 0)
    assert kw == ({"header_png": header_png} if header_png else {})


def test_dry_run_preview_shows_the_saved_header_png(monkeypatch, tmp_path):
    monkeypatch.setattr(de, "PREVIEW", str(tmp_path / "preview.html"))
    monkeypatch.setattr(de, "load_data", lambda: DATA)
    assert de.main(["--dry-run"], now=NOW, header=lambda top, date: png()) == "preview"
    assert (tmp_path / "header.png").read_bytes() == png()
    preview = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert 'src="header.png"' in preview and "cid:" not in preview


def test_main_logs_why_a_custom_sender_gets_no_header(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(de, "LAST_SENT", str(tmp_path / "last_sent.txt"))
    monkeypatch.setattr(de, "load_data", lambda: DATA)
    assert de.main(["--force"], now=NOW, sleep=lambda s: None, send=lambda *a, **kw: "ok") == "sent"
    assert "[HEADER] No header: custom sender" in capsys.readouterr().out
