"""Sending, end to end: real MIMEMultipart, real as_string()/send_message(), real socket. Nothing mocks
the encoding or the wire.

Run 39970000707 died on `'ascii' codec can't encode character '\\xa0' in position 28`. That is smtplib's
AUTH PLAIN string, not the Subject header: `"\\0%s\\0%s" % (user, password)` with a 22-character GMAIL_USER
puts the password at index 24, and an app password pasted from Google's UI carries NBSP separators.
test_auth_plain_* pins that. The header tests cover the second, latent failure on the same path.
"""
import _socket
import base64
import email
import ipaddress
import socket
import socketserver
import smtplib
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.header import Header, decode_header, make_header
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import send_daily_email as de

# The exact subject shape that failed in CI: em dashes from the template, NBSP from the headline.
HOSTILE = "Scout Daily — Fri 23 Oct 2026 —\xa0Microsoft to expand Azure Maia system"
CLEANED = "Scout Daily — Fri 23 Oct 2026 — Microsoft to expand Azure Maia system"


class _SMTPHandler(socketserver.StreamRequestHandler):
    """Enough of RFC 5321 for smtplib to deliver one message and for us to keep the bytes.

    Advertises AUTH PLAIN so the real login() path runs, and refuses any line carrying a byte above 0x7f:
    a conforming SMTP session for this message is 7-bit throughout (headers are RFC 2047, bodies base64),
    so a non-ASCII byte on the wire is the CI failure reaching the socket instead of raising locally."""

    def handle(self):
        self.wfile.write(b"220 localhost ESMTP\r\n")
        body, in_data = [], False
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if not line.isascii():
                self.server.non_ascii.append(line)
                self.wfile.write(b"500 non-ascii byte on the wire\r\n")
                return
            if in_data:
                if line in (b".\r\n", b".\n"):
                    in_data = False
                    self.server.messages.append(b"".join(body))
                    self.wfile.write(b"250 OK\r\n")
                else:
                    body.append(line)
                continue
            cmd = line.upper()
            if cmd.startswith(b"DATA"):
                in_data = True
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif cmd.startswith(b"QUIT"):
                self.wfile.write(b"221 Bye\r\n")
                return
            elif cmd.startswith(b"EHLO"):
                self.wfile.write(b"250-localhost\r\n250 AUTH PLAIN\r\n")
            elif cmd.startswith(b"RCPT"):
                self.server.rcpt.append(line.split(b"<", 1)[1].split(b">", 1)[0])
                self.wfile.write(b"250 OK\r\n")
            elif cmd.startswith(b"AUTH PLAIN"):
                self.server.auth.append(base64.b64decode(line.split(None, 2)[2].strip()))
                self.wfile.write(b"235 2.7.0 Authentication successful\r\n")
            else:  # HELO/MAIL
                self.wfile.write(b"250 localhost\r\n")


LOOPBACK = frozenset({ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")})


def _gate_loopback(monkeypatch):
    """tests/test_news_cache.py blocks sockets process-wide at import. Restore a real loopback connect for
    these tests only, going through the C implementation so it cannot be the patched Python one.

    Only 127.0.0.1 and ::1 are allowed: the unrestricted version handed these tests a working route to the
    whole internet, so a stray hostname would have left the suite making real network calls."""
    # Default must be the stdlib sentinel, not None: smtplib calls create_connection without a timeout,
    # which passes _GLOBAL_DEFAULT_TIMEOUT through, and settimeout() rejects that object.
    def _create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *a, **k):
        try:
            ip = ipaddress.ip_address(address[0])
        except ValueError:
            raise RuntimeError(f"loopback fixture: {address[0]!r} is not a literal IP address") from None
        if ip not in LOOPBACK:
            raise RuntimeError(f"loopback fixture refuses non-loopback address {address[0]!r}")
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        if timeout not in (None, socket._GLOBAL_DEFAULT_TIMEOUT):
            sock.settimeout(timeout)
        _socket.socket.connect(sock, address)
        return sock

    monkeypatch.setattr(socket, "create_connection", _create_connection)
    # smtplib.SMTP() calls getfqdn() for EHLO, a reverse lookup the connect gate never sees. A dotless fqdn makes
    # it fall back to gethostbyname(gethostname()), so pin the hostname too: only "localhost" is ever resolved.
    monkeypatch.setattr(socket, "getfqdn", lambda name="": "localhost")
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("localhost", [], [ip]))
    monkeypatch.setattr(socket, "gethostname", lambda: "localhost")


@pytest.fixture
def loopback(monkeypatch):
    _gate_loopback(monkeypatch)


@pytest.fixture
def smtp_server(loopback):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPHandler)
    server.messages, server.auth, server.non_ascii, server.rcpt = [], [], [], []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_loopback_fixture_refuses_a_public_address(loopback):
    with pytest.raises(RuntimeError, match="refuses non-loopback address"):
        socket.create_connection(("1.1.1.1", 53), timeout=5)


def test_loopback_fixture_allows_both_loopback_literals(loopback):
    """Not a connect: asserts the gate's verdict, so the allowed set cannot silently shrink to nothing."""
    for allowed in ("127.0.0.1", "::1"):
        assert ipaddress.ip_address(allowed) in LOOPBACK
    with pytest.raises(RuntimeError, match="not a literal IP address"):
        socket.create_connection(("smtp.gmail.com", 465), timeout=5)


def _subject_header(raw):
    return email.message_from_bytes(raw)["Subject"]


PASTED_PASSWORD = "abcd\xa0efgh\xa0ijkl\xa0mnop"  # 16-char app password, NBSP separators from Google's UI
CI_USER = "ahmed94farah@gmail.com"  # 22 chars, so the password starts at index 24


def _auth_plain(user, password):
    """What smtplib.SMTP.auth() ascii-encodes before base64."""
    return ("\0%s\0%s" % (user, password)).encode("ascii")


def test_auth_plain_reproduces_the_ci_failure_without_cleaning():
    with pytest.raises(UnicodeEncodeError) as e:
        _auth_plain(CI_USER, PASTED_PASSWORD)
    assert "'ascii' codec can't encode character '\\xa0' in position 28" in str(e.value)


def test_auth_plain_succeeds_once_the_secret_is_cleaned():
    assert de.clean_secret(PASTED_PASSWORD) == "abcdefghijklmnop"
    assert _auth_plain(CI_USER, de.clean_secret(PASTED_PASSWORD)) == b"\0ahmed94farah@gmail.com\0abcdefghijklmnop"
    assert de.clean_secret(" user@example.com\n") == "user@example.com"
    assert de.clean_secret(None) == ""


def test_clean_header_folds_nbsp_and_drops_control_chars():
    assert de.clean_header(HOSTILE) == CLEANED
    assert de.clean_header("a\x00b\x1fc\x7fd") == "a b c d"
    assert de.clean_header("line\r\nBcc: attacker@example.com") == "line Bcc: attacker@example.com"
    assert de.clean_header(None) == ""


def test_as_string_serialises_a_hostile_subject():
    """Real serialisation of a subject carrying em dashes and NBSP; asserts it stays RFC 2047 and decodable."""
    msg = de.build_message(HOSTILE, "<p>hi</p>", "hi", "from@example.com", "to@example.com")
    raw = msg.as_string()

    header = raw.split("Subject: ", 1)[1].split("\n", 1)[0]
    assert header.isascii(), header
    assert "=?utf-8?" in header
    assert str(make_header(decode_header(_subject_header(raw.encode())))) == CLEANED


def test_message_reaches_a_real_server_with_a_decodable_subject(smtp_server):
    msg = de.build_message(HOSTILE, "<p>hi</p>", "hi", "from@example.com", "to@example.com")
    host, port = smtp_server.server_address
    with smtplib.SMTP(host, port, timeout=10) as client:
        assert client.send_message(msg) == {}

    assert len(smtp_server.messages) == 1
    raw = smtp_server.messages[0]
    assert b"\xa0" not in raw
    received = email.message_from_bytes(raw)
    assert str(make_header(decode_header(received["Subject"]))) == CLEANED
    types = {part.get_content_type() for part in received.walk()}
    assert {"text/plain", "text/html"} <= types


def test_ascii_subject_still_round_trips(smtp_server):
    msg = de.build_message("Plain ASCII subject", "<p>hi</p>", "hi", "from@example.com", "to@example.com")
    host, port = smtp_server.server_address
    with smtplib.SMTP(host, port, timeout=10) as client:
        client.send_message(msg)
    received = email.message_from_bytes(smtp_server.messages[0])
    assert str(make_header(decode_header(received["Subject"]))) == "Plain ASCII subject"


def test_clean_secret_strips_zero_width_space_and_bom():
    """U+200B and U+FEFF are category Cf, not \\s: the whitespace pass alone leaves them in the secret."""
    assert de.clean_secret("abcd​efgh​ijkl​mnop") == "abcdefghijklmnop"
    assert de.clean_secret("﻿abcd efgh﻿") == "abcdefgh"
    assert de.clean_secret("​﻿") == ""
    for cleaned in (de.clean_secret("abcd​efgh"), de.clean_secret("﻿user@example.com")):
        assert cleaned.isascii(), cleaned


def test_clean_header_strips_c1_control_characters():
    """cp1252-decoded copy-paste carries C1 bytes; \\s matches only U+0085 of that block."""
    assert de.clean_header("a\x80b\x9fc") == "a b c"
    assert de.clean_header("Scout\x85Daily") == "Scout Daily"
    assert de.clean_header(HOSTILE + "\x9d").isascii() is False  # em dashes stay, the C1 goes
    assert "\x9d" not in de.clean_header(HOSTILE + "\x9d")


def test_logged_subject_is_the_subject_that_gets_sent(smtp_server):
    """main() logs clean_header(subject); assert that string is byte-for-byte what the server decodes."""
    logged = de.clean_header(HOSTILE)
    msg = de.build_message(HOSTILE, "<p>hi</p>", "hi", "from@example.com", "to@example.com")
    host, port = smtp_server.server_address
    with smtplib.SMTP(host, port, timeout=10) as client:
        client.send_message(msg)
    received = email.message_from_bytes(smtp_server.messages[0])
    assert str(make_header(decode_header(received["Subject"]))) == logged == CLEANED


# Every separator Google's UI and a browser copy can leave in a pasted app password.
SEPARATORS = {"nbsp-00a0": "\xa0", "nnbsp-202f": " ", "zwsp-200b": "​", "bom-feff": "﻿"}


@pytest.mark.parametrize("sep", sorted(SEPARATORS), ids=sorted(SEPARATORS))
def test_smtp_send_logs_in_and_delivers_over_loopback(smtp_server, monkeypatch, sep):
    """The real smtp_send, the real smtplib AUTH PLAIN, a real socket: the path run 39970000707 died on."""
    monkeypatch.setenv("GMAIL_USER", CI_USER)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", SEPARATORS[sep].join(["abcd", "efgh", "ijkl", "mnop"]))
    monkeypatch.setenv("REPORT_EMAIL", "to@example.com")
    host, port = smtp_server.server_address

    result = de.smtp_send(HOSTILE, "<p>hi</p>", "hi", smtp_factory=smtplib.SMTP, host=host, port=port)

    assert smtp_server.non_ascii == [], smtp_server.non_ascii
    assert smtp_server.auth == [b"\0" + CI_USER.encode() + b"\0abcdefghijklmnop"]
    assert len(smtp_server.messages) == 1
    raw = smtp_server.messages[0]
    assert b"\xa0" not in raw
    received = email.message_from_bytes(raw)
    assert str(make_header(decode_header(received["Subject"]))) == CLEANED
    assert received["To"] == "to@example.com"
    assert {"text/plain", "text/html"} <= {p.get_content_type() for p in received.walk()}
    assert "refused=none" in result


def test_smtp_send_defaults_stay_on_the_production_path():
    """The seams must not change production: gmail over implicit TLS, nothing else."""
    import inspect
    defaults = inspect.signature(de.smtp_send).parameters
    assert defaults["smtp_factory"].default is smtplib.SMTP_SSL
    assert defaults["host"].default == "smtp.gmail.com"
    assert defaults["port"].default == 465
    assert all(defaults[n].kind is inspect.Parameter.KEYWORD_ONLY for n in ("smtp_factory", "host", "port"))


def test_main_logs_the_subject_that_goes_on_the_wire(tmp_path, monkeypatch, capsys):
    """The real main(), a headline with NBSP and BEL: the [EMAIL] Subject line must match the decoded wire subject."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=de.LONDON)
    news = [{"title": "Maia\xa0launch\x07 today" if i == 0 else f"News {i}", "summary": "One. Two. Three.",
             "enterprise_use": "Use.", "url": f"https://news.example/{i}", "relevance_score": 10 - i,
             "source": "OpenAI", "category": "Agents"} for i in range(8)]
    papers = [{"title": f"Paper {i}", "summary": "P.", "url": f"https://arxiv.org/abs/{i}", "total_score": 60 - 10 * i}
              for i in range(4)]
    monkeypatch.setattr(de, "load_data", lambda: {"news": news, "papers": papers, "reason": None,
                                                  "newest": now.astimezone(timezone.utc) - timedelta(hours=5)})
    monkeypatch.setattr(de, "LAST_SENT", str(tmp_path / "last_sent.txt"))
    monkeypatch.setattr(de, "PREVIEW", str(tmp_path / "preview.html"))
    received = []

    def fake_send(subject, html_body, text_body):
        received.append(subject)
        msg = de.build_message(subject, html_body, text_body, "from@example.com", "to@example.com")
        received.append(str(make_header(decode_header(email.message_from_string(msg.as_string())["Subject"]))))
        return "250 OK (fake)"

    assert de.main(["--force"], now=now, sleep=lambda s: None, send=fake_send) == "sent"
    raw, on_the_wire = received
    assert "\xa0" in raw and "\x07" in raw  # the hostile headline reached the subject
    logged = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[EMAIL] Subject: ")]
    assert logged == ["[EMAIL] Subject: " + on_the_wire]


class _SpySMTP:
    """Wraps a real smtplib.SMTP and records only the calls smtp_send makes on it, not smtplib's internal ones."""

    def __init__(self, host, port):
        self._smtp, self.calls = smtplib.SMTP(host, port, timeout=10), []
        _SpySMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._smtp.__exit__(*exc)

    def __getattr__(self, name):
        self.calls.append(name)
        return getattr(self._smtp, name)


def test_smtp_send_cleans_report_email_and_uses_send_message(smtp_server, monkeypatch):
    monkeypatch.setenv("GMAIL_USER", CI_USER)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")
    monkeypatch.setenv("REPORT_EMAIL", "\ufeffto@example.com\xa0")  # BOM and NBSP from a pasted secret
    host, port = smtp_server.server_address

    de.smtp_send("s", "<p>hi</p>", "hi", smtp_factory=_SpySMTP, host=host, port=port)

    assert smtp_server.rcpt == [b"to@example.com"]
    assert email.message_from_bytes(smtp_server.messages[0])["To"] == "to@example.com"
    assert "send_message" in _SpySMTP.last.calls and "sendmail" not in _SpySMTP.last.calls


def test_subject_is_an_explicit_utf8_header():
    """Pins the Header object: compat32's implicit fallback also yields =?utf-8?, so the wire tests cannot tell."""
    subject = de.build_message("Plain ASCII", "<p>hi</p>", "hi", "from@example.com", "to@example.com")["Subject"]
    assert isinstance(subject, Header)
    assert subject.encode().startswith("=?utf-8?")


def test_loopback_gate_restores_create_connection():
    original = socket.create_connection
    with pytest.MonkeyPatch.context() as mp:
        _gate_loopback(mp)
        assert socket.create_connection is not original
    assert socket.create_connection is original
