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
        {"slot": "title", "color": "rgb(241, 245, 249)", "bg": "rgb(15, 17, 23)", "lines": 2},
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


def test_colour_gate_passes_the_filled_template_and_fails_off_brand():
    page = rh.fill("Email", "A title", "Tue 24 Nov 2026")
    assert gates.colour_gate(page) == []
    assert gates.colour_gate(page.replace("#00ff88", "#ff0000")) == ["non-brand colour #ff0000"]
    assert gates.colour_gate(page + "<p style='color:rgb(1,2,3)'>")


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
    assert msg.get_content_type() == "multipart/related"
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
