"""Read-only IMAP and MIME handling."""

from __future__ import annotations

import base64
import hashlib
import imaplib
import os
import re
import ssl
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

DEFAULT_MAX_MESSAGE_BYTES = 30 * 1024 * 1024
DEFAULT_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
DEFAULT_SOCKET_TIMEOUT = 30.0


class MailError(RuntimeError):
    """An IMAP message cannot be read safely."""


class MessageTooLarge(MailError):
    pass


class AttachmentTooLarge(MailError):
    pass


@dataclass(frozen=True)
class ImapSettings:
    host: str
    port: int
    tls_server_name: str
    username: str
    password: str
    ca_file: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ImapSettings:
        values = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = values.get(name, "")
            if not value:
                raise MailError(f"{name} is required")
            return value

        raw_port = values.get("IMAP_PORT", "993")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise MailError("IMAP_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise MailError("IMAP_PORT must be between 1 and 65535")
        return cls(
            host=required("IMAP_HOST"),
            port=port,
            tls_server_name=values.get(
                "IMAP_TLS_SERVER_NAME", "mail.terminus.home.arpa"
            ),
            username=required("IMAP_USERNAME"),
            password=required("IMAP_PASSWORD"),
            ca_file=values.get("SSL_CERT_FILE") or None,
        )

    def ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=self.ca_file)


@dataclass(frozen=True)
class FolderSnapshot:
    uidvalidity: int
    uids: tuple[int, ...]


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class ParsedMessage:
    uid: int
    when: datetime
    from_addresses: tuple[str, ...]
    subject: str
    attachments: tuple[Attachment, ...]


class _TLSNamedIMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(
        self,
        host: str,
        port: int,
        *,
        tls_server_name: str,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> None:
        self._tls_server_name = tls_server_name
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout: float) -> ssl.SSLSocket:
        sock = imaplib.IMAP4._create_socket(self, timeout)
        try:
            return self.ssl_context.wrap_socket(
                sock, server_hostname=self._tls_server_name
            )
        except Exception:
            sock.close()
            raise


ConnectionFactory = Callable[[ImapSettings, ssl.SSLContext, float], imaplib.IMAP4]


def _default_connection_factory(
    settings: ImapSettings,
    context: ssl.SSLContext,
    timeout: float,
) -> imaplib.IMAP4:
    return _TLSNamedIMAP4_SSL(
        settings.host,
        settings.port,
        tls_server_name=settings.tls_server_name,
        ssl_context=context,
        timeout=timeout,
    )


class ImapClient:
    """A minimal stateful IMAP client; fetch follows a read-only select."""

    def __init__(
        self,
        settings: ImapSettings,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
        socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
        connection_factory: ConnectionFactory = _default_connection_factory,
    ) -> None:
        if max_message_bytes <= 0 or max_attachment_bytes <= 0 or socket_timeout <= 0:
            raise ValueError("size limits and socket timeout must be positive")
        self.settings = settings
        self.max_message_bytes = max_message_bytes
        self.max_attachment_bytes = max_attachment_bytes
        self.socket_timeout = socket_timeout
        self._connection_factory = connection_factory
        self._connection: imaplib.IMAP4 | None = None
        self._selected_uidvalidity: int | None = None

    def __enter__(self) -> Self:
        connection: imaplib.IMAP4 | None = None
        try:
            connection = self._connection_factory(
                self.settings,
                self.settings.ssl_context(),
                self.socket_timeout,
            )
            status, _ = connection.login(self.settings.username, self.settings.password)
            if status != "OK":
                raise MailError("IMAP login failed")
            self._connection = connection
            return self
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
            self._connection = None
            if connection is not None:
                try:
                    connection.shutdown()
                except (imaplib.IMAP4.error, OSError):
                    pass
            raise MailError(f"cannot connect to IMAP: {exc}") from exc
        except MailError:
            if connection is not None:
                try:
                    connection.shutdown()
                except (imaplib.IMAP4.error, OSError):
                    pass
            raise

    def __exit__(self, *_: object) -> None:
        connection, self._connection = self._connection, None
        self._selected_uidvalidity = None
        if connection is not None:
            try:
                connection.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    @property
    def connection(self) -> imaplib.IMAP4:
        if self._connection is None:
            raise MailError("IMAP client is not connected")
        return self._connection

    def list_folders(self) -> tuple[str, ...]:
        try:
            status, data = self.connection.list()
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"cannot list IMAP folders: {exc}") from exc
        if status != "OK":
            raise MailError("cannot list IMAP folders")
        return tuple(
            _parse_list_mailbox(item) for item in data or () if isinstance(item, bytes)
        )

    def select(self, mailbox: str) -> FolderSnapshot:
        quoted = _quote_mailbox(mailbox)
        try:
            status, _ = self.connection.select(quoted, readonly=True)
            if status != "OK":
                raise MailError(f"cannot select mailbox {mailbox!r}")
            _, validity_data = self.connection.response("UIDVALIDITY")
            validity = _first_integer(validity_data, "UIDVALIDITY")
            status, data = self.connection.uid("SEARCH", None, "ALL")
            if status != "OK":
                raise MailError(f"cannot enumerate mailbox {mailbox!r}")
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"cannot select mailbox {mailbox!r}: {exc}") from exc
        raw_uids = b" ".join(item for item in (data or ()) if isinstance(item, bytes))
        try:
            uids = tuple(int(uid) for uid in raw_uids.split())
        except ValueError as exc:
            raise MailError("IMAP returned an invalid UID list") from exc
        self._selected_uidvalidity = validity
        return FolderSnapshot(validity, uids)

    def fetch(self, uid: int, local_timezone: ZoneInfo) -> ParsedMessage:
        if self._selected_uidvalidity is None:
            raise MailError("select a mailbox before fetching messages")
        if uid <= 0:
            raise ValueError("UID must be positive")
        try:
            status, metadata = self.connection.uid(
                "FETCH", str(uid), "(RFC822.SIZE INTERNALDATE)"
            )
            if status != "OK":
                raise MailError(f"cannot fetch metadata for UID {uid}")
            metadata_line = _response_line(metadata)
            size = _metadata_integer(
                metadata_line, rb"RFC822\.SIZE\s+(\d+)", "RFC822.SIZE"
            )
            internal_date = _parse_internal_date(metadata_line)
            if size > self.max_message_bytes:
                raise MessageTooLarge(
                    f"UID {uid} is {size} bytes; limit is {self.max_message_bytes} bytes"
                )
            section = f"(BODY.PEEK[]<0.{self.max_message_bytes + 1}>)"
            status, body_data = self.connection.uid("FETCH", str(uid), section)
            if status != "OK":
                raise MailError(f"cannot fetch body for UID {uid}")
            raw = _response_body(body_data)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"cannot fetch UID {uid}: {exc}") from exc
        if len(raw) > self.max_message_bytes:
            raise MessageTooLarge(
                f"UID {uid} body is {len(raw)} bytes; limit is {self.max_message_bytes} bytes"
            )
        return parse_message(
            raw,
            uid=uid,
            internal_date=internal_date,
            local_timezone=local_timezone,
            max_attachment_bytes=self.max_attachment_bytes,
        )


def _quote_mailbox(mailbox: str) -> str:
    if not mailbox or "\r" in mailbox or "\n" in mailbox or "\x00" in mailbox:
        raise MailError("mailbox name is empty or contains a control character")
    encoded = _encode_modified_utf7(mailbox)
    return '"' + encoded.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _first_integer(data: list[bytes] | tuple[bytes, ...] | None, name: str) -> int:
    line = b" ".join(item for item in (data or ()) if isinstance(item, bytes))
    match = re.search(rb"\d+", line)
    if match is None:
        raise MailError(f"IMAP did not return {name}")
    return int(match.group())


def _response_line(data: list[object] | tuple[object, ...] | None) -> bytes:
    for item in data or ():
        if isinstance(item, bytes):
            return item
        if isinstance(item, tuple) and item and isinstance(item[0], bytes):
            return item[0]
    raise MailError("IMAP returned an empty FETCH response")


def _response_body(data: list[object] | tuple[object, ...] | None) -> bytes:
    for item in data or ():
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    raise MailError("IMAP returned no message body")


def _metadata_integer(line: bytes, pattern: bytes, name: str) -> int:
    match = re.search(pattern, line, re.IGNORECASE)
    if match is None:
        raise MailError(f"IMAP did not return {name}")
    return int(match.group(1))


def _parse_internal_date(line: bytes) -> datetime:
    match = re.search(rb'INTERNALDATE\s+"([^"]+)"', line, re.IGNORECASE)
    if match is None:
        raise MailError("IMAP did not return INTERNALDATE")
    try:
        return datetime.strptime(match.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except (UnicodeDecodeError, ValueError) as exc:
        raise MailError("IMAP returned an invalid INTERNALDATE") from exc


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def parse_message(
    raw: bytes,
    *,
    uid: int,
    internal_date: datetime,
    local_timezone: ZoneInfo,
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
) -> ParsedMessage:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = _decode_header(message.get("Subject"))
    sender_headers = [_decode_header(value) for value in message.get_all("From", [])]
    from_addresses = tuple(
        address.casefold() for _, address in getaddresses(sender_headers) if address
    )
    when = internal_date
    try:
        raw_date = str(message.get("Date", ""))
        header_date = parsedate_to_datetime(raw_date)
        if header_date is not None and header_date.tzinfo is not None:
            when = header_date
        elif header_date is not None and re.search(r"(?:^|\s)-0000\s*$", raw_date):
            when = header_date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    when = when.astimezone(local_timezone)

    attachments: list[Attachment] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        raw_filename = part.get_filename()
        filename = _decode_header(raw_filename) if raw_filename else ""
        if disposition == "inline" or not (disposition == "attachment" or filename):
            continue
        content_type = part.get_content_type().casefold()
        pdf_candidate = content_type == "application/pdf" or (
            content_type == "application/octet-stream"
            and filename.casefold().endswith(".pdf")
        )
        if not pdf_candidate:
            continue
        try:
            content = part.get_payload(decode=True)
        except (TypeError, ValueError) as exc:
            raise MailError(f"UID {uid} has an invalid PDF attachment payload") from exc
        if not isinstance(content, bytes):
            raise MailError(f"UID {uid} has an invalid PDF attachment payload")
        if any(
            type(defect).__name__.startswith("InvalidBase64") for defect in part.defects
        ):
            raise MailError(f"UID {uid} has an invalid PDF attachment encoding")
        if not content.startswith(b"%PDF-"):
            raise MailError(
                f"UID {uid} has an attachment labeled as PDF without a PDF signature"
            )
        if len(content) > max_attachment_bytes:
            raise AttachmentTooLarge(
                f"UID {uid} attachment exceeds the {max_attachment_bytes}-byte limit"
            )
        safe_name = sanitize_filename(filename or "attachment.pdf")
        if not safe_name.casefold().endswith(".pdf"):
            safe_name += ".pdf"
        attachments.append(
            Attachment(safe_name, content, hashlib.sha256(content).hexdigest())
        )
    return ParsedMessage(uid, when, from_addresses, subject, tuple(attachments))


def _truncate_utf8(value: str, max_bytes: int) -> str:
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    return value.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


def sanitize_filename(filename: str, *, max_length: int = 160) -> str:
    name = unicodedata.normalize("NFKC", filename).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        character for character in name if character >= " " and character != "\x7f"
    )
    name = re.sub(r"[/:*?\"<>|]", "_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = "attachment.pdf"
    suffix = Path(name).suffix
    if len(name.encode("utf-8")) > max_length:
        suffix_bytes = len(suffix.encode("utf-8"))
        if suffix and suffix_bytes < max_length:
            name = (
                _truncate_utf8(name[: -len(suffix)], max_length - suffix_bytes).rstrip(
                    " ."
                )
                + suffix
            )
        else:
            name = _truncate_utf8(name, max_length).rstrip(" .")
    return name


def archive_filename(filename: str, content_hash: str, when: datetime) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", content_hash):
        raise ValueError("content_hash must be a SHA-256 hex digest")
    safe = sanitize_filename(filename)
    path = Path(safe)
    suffix = path.suffix if path.suffix.casefold() == ".pdf" else ".pdf"
    stem = path.stem if path.suffix else path.name
    stem = _truncate_utf8(stem, 120).rstrip(" .") or "attachment"
    return f"{when.date().isoformat()}-{stem}-{content_hash[:12].lower()}{suffix}"


def _parse_list_mailbox(line: bytes) -> str:
    match = re.match(rb'^\([^)]*\)\s+(?:NIL|"(?:\\.|[^"])*")\s+(.+)$', line)
    if match is None:
        raise MailError("IMAP returned an invalid LIST response")
    raw_name = match.group(1).strip()
    if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
        raw_name = re.sub(rb"\\(.)", rb"\1", raw_name[1:-1])
    try:
        return _decode_modified_utf7(raw_name.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MailError("IMAP returned an invalid mailbox name") from exc


def _encode_modified_utf7(value: str) -> str:
    result: list[str] = []
    non_ascii: list[str] = []

    def flush() -> None:
        if not non_ascii:
            return
        encoded = base64.b64encode("".join(non_ascii).encode("utf-16-be")).decode(
            "ascii"
        )
        result.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
        non_ascii.clear()

    for character in value:
        if " " <= character <= "~":
            flush()
            result.append("&-" if character == "&" else character)
        else:
            non_ascii.append(character)
    flush()
    return "".join(result)


def _decode_modified_utf7(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "&":
            result.append(value[index])
            index += 1
            continue
        end = value.find("-", index)
        if end < 0:
            raise ValueError("unterminated modified UTF-7 sequence")
        encoded = value[index + 1 : end]
        if not encoded:
            result.append("&")
        else:
            encoded = encoded.replace(",", "/")
            encoded += "=" * (-len(encoded) % 4)
            result.append(base64.b64decode(encoded).decode("utf-16-be"))
        index = end + 1
    return "".join(result)
