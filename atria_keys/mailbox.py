from __future__ import annotations

import email
import imaplib
import os
import re
import time
from dataclasses import dataclass
from email.message import Message
from typing import Protocol

from .config import MailboxConfig
from .errors import TransientProvisioningError


LINK_RE = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
OTP_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


@dataclass(frozen=True, slots=True)
class Verification:
    recipient: str
    link: str | None = None
    otp: str | None = None


class MailboxReader(Protocol):
    def prepare_address(self, run_id: str) -> str: ...

    def wait_for_verification(self, recipient: str) -> Verification: ...


def extract_verification(message: Message, recipient: str) -> Verification | None:
    recipients = " ".join(
        str(message.get(header, ""))
        for header in ("To", "Delivered-To", "X-Original-To", "Envelope-To")
    ).lower()
    if recipient.lower() not in recipients:
        return None

    chunks: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() in {"text/plain", "text/html"}:
                try:
                    chunks.append(part.get_content())
                except (AttributeError, LookupError, UnicodeDecodeError):
                    payload = part.get_payload(decode=True) or b""
                    chunks.append(payload.decode("utf-8", "replace"))
    else:
        try:
            chunks.append(message.get_content())
        except (AttributeError, LookupError, UnicodeDecodeError):
            payload = message.get_payload(decode=True) or b""
            chunks.append(payload.decode("utf-8", "replace"))

    body = "\n".join(chunks)
    links = [link.rstrip(".,);&quot;") for link in LINK_RE.findall(body)]
    preferred = next(
        (link for link in links if any(word in link.lower() for word in ("verify", "confirm", "callback"))),
        links[0] if links else None,
    )
    otp_match = OTP_RE.search(body)
    if not preferred and not otp_match:
        return None
    return Verification(recipient, preferred, otp_match.group(1) if otp_match else None)


class CatchAllImapMailboxReader:
    """Poll a catch-all IMAP inbox and match the exact envelope recipient."""

    def __init__(self, config: MailboxConfig):
        self.config = config

    def prepare_address(self, run_id: str) -> str:
        local = re.sub(r"[^a-z0-9]", "", run_id.lower())[:20]
        return f"atria-{local}@{self.config.recipient_domain}"

    def _connect(self) -> imaplib.IMAP4:
        password = os.environ.get(self.config.password_env)
        if not password:
            raise TransientProvisioningError(
                "mailbox_credentials_missing",
                f"environment variable {self.config.password_env} is not set",
                stage="mailbox",
            )
        cls = imaplib.IMAP4_SSL if self.config.use_ssl else imaplib.IMAP4
        client = cls(self.config.host, self.config.port)
        client.login(self.config.username, password)
        client.select("INBOX")
        return client

    def wait_for_verification(self, recipient: str) -> Verification:
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            client: imaplib.IMAP4 | None = None
            try:
                client = self._connect()
                status, ids = client.search(None, "UNSEEN")
                if status != "OK":
                    raise imaplib.IMAP4.error("UNSEEN search failed")
                for message_id in reversed(ids[0].split()):
                    status, rows = client.fetch(message_id, "(RFC822)")
                    if status != "OK" or not rows:
                        continue
                    raw = next((row[1] for row in rows if isinstance(row, tuple)), None)
                    if not raw:
                        continue
                    match = extract_verification(email.message_from_bytes(raw), recipient)
                    if match:
                        return match
            except (OSError, imaplib.IMAP4.error):
                pass
            finally:
                if client is not None:
                    try:
                        client.logout()
                    except (OSError, imaplib.IMAP4.error):
                        pass
            time.sleep(self.config.poll_seconds)
        raise TransientProvisioningError(
            "verification_timeout",
            "verification message did not arrive before the configured timeout",
            stage="key_extract",
        )


class FixtureMailboxReader:
    def __init__(self, domain: str = "fixture.invalid"):
        self.domain = domain

    def prepare_address(self, run_id: str) -> str:
        return f"atria-{run_id[:12]}@{self.domain}"

    def wait_for_verification(self, recipient: str) -> Verification:
        return Verification(recipient, link="fixture://verified")
