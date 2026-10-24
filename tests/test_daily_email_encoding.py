"""Sending, end to end: real MIMEMultipart, real as_string()/send_message(), real socket. Nothing mocks
the encoding or the wire.

Run 39970000707 died on `'ascii' codec can't encode character '\\xa0' in position 28`. That is smtplib's
AUTH PLAIN string, not the Subject header: `"\\0%s\\0%s" % (user, password)` with a 22-character GMAIL_USER
puts the password at index 24, and an app password pasted from Google's UI carries NBSP separators.
test_auth_plain_* pins that. The header tests cover the second, latent failure on the same path.
"""
import _socket
import email
import socket
import socketserver
import smtplib
import sys
import threading
from email.header import decode_header, make_header
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import send_daily_email as de

# The exact subject shape that failed in CI: em dashes from the template, NBSP from the headline.
HOSTILE = "Scout Daily — Fri 23 Oct 2026 —\xa0Microsoft to expand Azure Maia system"
CLEANED = "Scout Daily — Fri 23 Oct 2026 — Microsoft to expand Azure Maia system"


class _SMTPHandler(socketserver.StreamRequestHandler):
    """Enough of RFC 5321 for smtplib to deliver one message and for us to keep the bytes."""

    def handle(self):
        self.wfile.write(b"220 localhost ESMTP\r\n")
        body, in_data = [], False
        while True:
            line = self.rfile.readline()
            if not line:
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
            else:  # EHLO/HELO/MAIL/RCPT
                self.wfile.write(b"250 localhost\r\n")


@pytest.fixture
def loopback(monkeypatch):
    """tests/test_news_cache.py blocks sockets process-wide at import. Restore a real loopback connect for
    these tests only, going through the C implementation so it cannot be the patched Python one."""
    def _create_connection(address, timeout=None, *a, **k):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if timeout is not None:
            sock.settimeout(timeout)
        _socket.socket.connect(sock, address)
        return sock

    monkeypatch.setattr(socket, "create_connection", _create_connection)


@pytest.fixture
def smtp_server(loopback):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPHandler)
    server.messages = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


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
