"""A minimal, deliberately non-production SMTP server for tests only.

Speaks just enough SMTP (EHLO/STARTTLS/AUTH LOGIN/MAIL/RCPT/DATA/QUIT) to
drive SmtpProvider through success/failure scenarios without any real
network access -- always bound to 127.0.0.1 on an ephemeral port. Never
imported by production code.
"""

from __future__ import annotations

import base64
import datetime
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field


def generate_self_signed_cert() -> tuple[bytes, bytes]:
    """Generate an in-memory self-signed cert/key pair for "localhost"."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
    )
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@dataclass
class FakeSmtpServer:
    """One-shot fake SMTP server for a single test connection.

    mode: "starttls" (plaintext then upgrade), "implicit_tls" (TLS from
        accept()), or "no_starttls" (EHLO never advertises STARTTLS).
    auth_ok: whether AUTH LOGIN succeeds.
    recipient_ok: whether RCPT TO is accepted.
    """

    mode: str = "starttls"
    auth_ok: bool = True
    recipient_ok: bool = True
    hang_after_data_prompt: bool = False
    host: str = "127.0.0.1"
    port: int = field(default=0, init=False)
    transcript: list[tuple[bool, str]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._cert_pem, self._key_pem = generate_self_signed_cert()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass

    def _tls_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            cert_path = Path(tmp) / "cert.pem"
            key_path = Path(tmp) / "key.pem"
            cert_path.write_bytes(self._cert_pem)
            key_path.write_bytes(self._key_pem)
            ctx.load_cert_chain(str(cert_path), str(key_path))
        return ctx

    def _serve(self) -> None:
        # Loop accepting connections until stop() closes the listening
        # socket: a test's lifecycle (connect/validate, then a separate
        # test-send, then possibly a config-update) opens more than one
        # connection to this fixture, not just one.
        while True:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            self._handle_connection(conn)

    def _handle_connection(self, conn: socket.socket) -> None:
        tls_active = self.mode == "implicit_tls"
        try:
            if self.mode == "implicit_tls":
                conn = self._tls_context().wrap_socket(conn, server_side=True)

            rfile = conn.makefile("rb")

            def send(line: str) -> None:
                conn.sendall((line + "\r\n").encode("ascii"))

            def recv_line() -> str:
                raw = rfile.readline()
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self.transcript.append((tls_active, text))
                return text

            send("220 fake.smtp.test ESMTP")

            while True:
                line = recv_line()
                upper = line.upper()

                if upper.startswith("EHLO") or upper.startswith("HELO"):
                    send("250-fake.smtp.test")
                    if self.mode != "no_starttls" and not tls_active:
                        send("250-STARTTLS")
                    send("250 AUTH LOGIN PLAIN")
                elif upper.startswith("STARTTLS"):
                    send("220 Go ahead")
                    conn = self._tls_context().wrap_socket(conn, server_side=True)
                    rfile = conn.makefile("rb")
                    tls_active = True
                elif upper.startswith("AUTH LOGIN"):
                    send(
                        "334 " + base64.b64encode(b"Username:").decode("ascii")
                    )
                    recv_line()  # username (base64)
                    send(
                        "334 " + base64.b64encode(b"Password:").decode("ascii")
                    )
                    recv_line()  # password (base64)
                    if self.auth_ok:
                        send("235 Authentication successful")
                    else:
                        send("535 Authentication credentials invalid")
                        break
                elif upper.startswith("MAIL FROM"):
                    send("250 OK")
                elif upper.startswith("RCPT TO"):
                    if self.recipient_ok:
                        send("250 OK")
                    else:
                        send("550 No such user here")
                elif upper.startswith("DATA"):
                    if not self.recipient_ok:
                        # No valid recipients: real servers reject this
                        # before DATA is ever sent (smtplib raises
                        # SMTPRecipientsRefused before issuing DATA).
                        send("554 No valid recipients")
                        continue
                    send("354 End data with <CR><LF>.<CR><LF>")
                    while True:
                        data_line = rfile.readline()
                        if data_line in (b".\r\n", b".\n"):
                            break
                        if not data_line:
                            break
                    if self.hang_after_data_prompt:
                        # Simulate a connection that goes silent after
                        # receiving the full message but before ever
                        # acknowledging it -- the client cannot know
                        # whether the message was accepted.
                        time.sleep(30)
                        break
                    send("250 OK: message accepted")
                elif upper.startswith("QUIT"):
                    send("221 Bye")
                    break
                else:
                    send("500 Command not recognized")
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
