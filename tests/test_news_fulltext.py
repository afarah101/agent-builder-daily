"""News full-text fetch against a local test server (no internet): wall-clock and size limits, address checks on
every hop, manual redirects, html only, snippet kept on any failure."""
import os
import socket
import sys
import threading
import time
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

for k, v in {"GROQ_API_KEY": "test-key-1", "GROQ_API_KEY_2": "", "LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
             "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_TRACING_ENABLED": "false", "LANGCHAIN_TRACING_V2": "false",
             "LANGSMITH_TRACING": "false", "LANGCHAIN_API_KEY": ""}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import industry_news_scout_langgraph as news

PAGE = b"<html><body><article><h1>Real story</h1>" + b"<p>The actual article body with real reporting in it.</p>" * 120 + b"</article></body></html>"
BIG = 20_000_000


class Handler(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        Handler.hits.append(self.path)
        p = unquote(self.path)
        if p == "/page":
            self._send(PAGE)
        elif p == "/pdf":
            self._send(b"%PDF-1.7 " + b"x" * 5000, ctype="application/pdf")
        elif p == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(BIG))
            self.end_headers()
            chunk = b"<p>" + b"a" * 65530 + b"</p>"
            try:
                for _ in range(BIG // len(chunk)):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif p == "/drip":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                for _ in range(30):  # one byte every 2 s for a minute: each read is quick, the total is not
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif p.startswith("/to/"):
            self._redirect(p[len("/to/"):].replace("__", "://", 1))
        elif p.startswith("/hop/"):
            n = int(p.rsplit("/", 1)[1])
            self._redirect("/page" if n == 0 else f"/hop/{n - 1}")
        else:
            self._send(b"not found", status=404)


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.fixture(autouse=True)
def real_sockets(monkeypatch):
    """Other test modules replace socket.socket.connect with a network blocker at import time; this module talks
    only to its own 127.0.0.1 server, so it restores the built-in connect for each test."""
    if "connect" in vars(socket.socket):
        monkeypatch.delattr(socket.socket, "connect")


@pytest.fixture
def web(server, monkeypatch):
    """`localhost` resolves to a public-looking address so local test pages pass the check; IP literals and every
    other name use the real resolver. Real DNS lookups of public names would need internet, so they are blocked."""
    real = news._resolve_ips

    def resolve(host, port):
        if host == "localhost":
            return {"93.184.216.34"}
        try:
            socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host.strip("[]"))
        except OSError:
            raise OSError(f"no internet in tests: {host}")
        return real(host, port)
    monkeypatch.setattr(news, "_resolve_ips", resolve)
    Handler.hits.clear()
    return f"http://localhost:{server}"


def fetch(url, snippet="Short teaser."):
    t0 = time.monotonic()
    out = news.fetch_source_text({"title": "T", "summary_raw": snippet, "url": url})
    return out, time.monotonic() - t0


def test_normal_page_returns_its_text(web):
    out, dt = fetch(web + "/page")
    assert out.startswith("Real story") and "actual article body" in out and dt < 5


def test_full_text_is_capped_at_3000(web, monkeypatch):
    monkeypatch.setattr(news.trafilatura, "extract", lambda *a, **k: "B" * 9000)
    out, _ = fetch(web + "/page")
    assert out == "B" * 3000


def test_long_snippet_is_not_fetched(web):
    snippet = "y" * 400
    out, _ = fetch(web + "/page", snippet)
    assert out == snippet and Handler.hits == []


def test_20mb_page_stops_at_2mb_in_under_11s(web):
    tracemalloc.start()
    out, dt = fetch(web + "/big")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"\n20MB page: {dt:.2f}s, peak traced memory {peak / 1e6:.1f} MB")
    assert out == "Short teaser." and dt < 11 and peak < 8_000_000  # never holds more than ~2 MB of the page


def test_slow_drip_stops_in_under_11s(web):
    out, dt = fetch(web + "/drip")
    assert out == "Short teaser." and dt < 11


@pytest.mark.parametrize("target", ["http__127.0.0.1/x", "http__10.0.0.1/x", "http__169.254.169.254/latest/meta-data",
                                    "http__[::1]/x", "http__[::ffff:127.0.0.1]/x", "ftp__example.com/x", "file__/etc/passwd"])
def test_redirect_to_private_or_non_http_is_refused(web, target, capsys):
    out, dt = fetch(web + "/to/" + quote(target, safe="/_:"))
    assert out == "Short teaser." and dt < 2
    assert len(Handler.hits) == 1  # only the redirecting page was requested; the target never was
    assert "PermissionError" in capsys.readouterr().out


def test_host_resolving_to_loopback_is_refused(server, capsys):
    out, _ = fetch(f"http://localhost:{server}/page")  # real resolver: localhost -> 127.0.0.1
    log = capsys.readouterr().out
    assert out == "Short teaser." and ("non-public address 127.0.0.1" in log or "non-public address ::1" in log)


def test_three_redirects_allowed_four_refused(web, capsys):
    ok, _ = fetch(web + "/hop/2")  # /hop/2 -> /hop/1 -> /hop/0 -> /page: 3 redirects
    assert ok.startswith("Real story")
    out, _ = fetch(web + "/hop/3")  # 4 redirects
    assert out == "Short teaser." and "more than 3 redirects" in capsys.readouterr().out


def test_non_html_is_skipped(web):
    out, _ = fetch(web + "/pdf")
    assert out == "Short teaser."


def test_404_keeps_the_snippet(web):
    out, _ = fetch(web + "/missing")
    assert out == "Short teaser."


def test_classify_uses_source_text_up_to_3000(monkeypatch):
    prompts = []
    monkeypatch.setattr(news, "groq_complete", lambda messages, **kw: prompts.append(messages[0]["content"]) or
                        '{"summary": "s", "enterprise_use": "e", "relevance_score": 7, "relevance_signal": "high", "category": "C"}')
    body = "B" * 5000
    news.classify_news({"raw_articles": [{"title": "T", "source": "S", "summary_raw": "short", "source_text": body, "url": "u"}],
                        "classified_news": [], "output_file": "x.json"})
    assert "B" * 3000 in prompts[0] and "B" * 3001 not in prompts[0]
