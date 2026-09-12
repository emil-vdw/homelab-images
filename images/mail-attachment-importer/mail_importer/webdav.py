from __future__ import annotations

import base64
import hashlib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Mapping


class WebDavError(RuntimeError):
    pass


class WebDavAuthenticationError(WebDavError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class _Response:
    status: int
    body: bytes
    headers: Mapping[str, str]


class WebDavClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        app_token: str,
        *,
        timeout: float = 30,
        retries: int = 2,
        readiness_timeout: float = 120,
        opener=None,
        sleep=None,
        monotonic=None,
    ) -> None:
        if timeout <= 0 or retries < 0 or readiness_timeout <= 0:
            raise ValueError("timeouts must be positive and retries must not be negative")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise WebDavError("WebDAV base URL must be an HTTPS URL without a query or fragment")
        if parsed.username is not None or parsed.password is not None:
            raise WebDavError("WebDAV credentials must not appear in the base URL")
        self.base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", "")
        )
        self.archive_root = self.base_url.rstrip("/")
        credentials = base64.b64encode(f"{username}:{app_token}".encode()).decode("ascii")
        self._authorization = f"Basic {credentials}"
        self.timeout = timeout
        self.retries = retries
        self.readiness_timeout = readiness_timeout
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic
        if opener is None:
            context = ssl.create_default_context()
            opener = urllib.request.build_opener(
                _NoRedirect(), urllib.request.HTTPSHandler(context=context)
            )
        self._opener = opener

    def ensure_collection(self, relative_path: str) -> None:
        segments = _path_segments(relative_path)
        for length in range(1, len(segments) + 1):
            path = "/".join(segments[:length])
            response = self._request("MKCOL", self._url(path, collection=True), retry=True)
            if response.status == 201:
                continue
            if response.status == 405 and self._collection_exists(path):
                continue
            raise WebDavError(f"WebDAV refused to create collection with HTTP {response.status}")

    def check_root(self) -> None:
        if not self._collection_exists_url(self.base_url):
            raise WebDavError("WebDAV base URL is not an accessible collection")

    def upload_create_only(self, relative_path: str, content: bytes, digest: str) -> None:
        if hashlib.sha256(content).hexdigest() != digest:
            raise WebDavError("local content digest does not match upload digest")
        url = self._url(relative_path)
        temporary_url = self._url(_temporary_path(relative_path, digest))
        existing = self._request("HEAD", url, retry=True, readiness=True)
        if existing.status == 200:
            self._accept_existing(url, digest, len(content))
            self._delete_temporary(temporary_url)
            return
        if existing.status != 404:
            raise WebDavError(f"WebDAV existence check failed with HTTP {existing.status}")

        temporary = self._request("HEAD", temporary_url, retry=True, readiness=True)
        if temporary.status == 200:
            if not self._existing_matches(temporary_url, digest, len(content)):
                self._put_temporary(temporary_url, content, digest)
        elif temporary.status == 404:
            self._put_temporary(temporary_url, content, digest)
        else:
            raise WebDavError(
                f"WebDAV temporary-file check failed with HTTP {temporary.status}"
            )

        try:
            response = self._request(
                "MOVE",
                temporary_url,
                headers={"Destination": url, "Overwrite": "F"},
                retry=False,
            )
        except (TimeoutError, OSError):
            self._accept_existing(url, digest, len(content))
            return

        if response.status == 201:
            self._accept_existing(url, digest, len(content))
            return
        if response.status in (409, 412):
            self._accept_existing(url, digest, len(content))
            self._delete_temporary(temporary_url)
            return
        if response.status == 204:
            raise WebDavError("WebDAV overwrote a file during a create-only move")
        raise WebDavError(f"WebDAV move failed with HTTP {response.status}")

    def _put_temporary(self, url: str, content: bytes, digest: str) -> None:
        checksum = hashlib.sha1(content, usedforsecurity=False).hexdigest()
        try:
            response = self._request(
                "PUT",
                url,
                body=content,
                headers={
                    "Content-Type": "application/pdf",
                    "OC-Checksum": f"SHA1:{checksum}",
                },
                retry=False,
            )
        except (TimeoutError, OSError):
            self._accept_existing(url, digest, len(content))
            return
        if response.status not in (201, 204):
            raise WebDavError(f"WebDAV temporary upload failed with HTTP {response.status}")
        self._accept_existing(url, digest, len(content))

    def _delete_temporary(self, url: str) -> None:
        response = self._request("DELETE", url, retry=True)
        if response.status not in (204, 404):
            raise WebDavError(f"WebDAV temporary cleanup failed with HTTP {response.status}")

    def _collection_exists(self, path: str) -> bool:
        return self._collection_exists_url(self._url(path, collection=True))

    def _collection_exists_url(self, url: str) -> bool:
        response = self._request(
            "PROPFIND",
            url,
            body=(
                b'<?xml version="1.0" encoding="utf-8"?>'
                b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
                b"</d:prop></d:propfind>"
            ),
            headers={"Content-Type": "application/xml", "Depth": "0"},
            retry=True,
        )
        if response.status not in (200, 207):
            return False
        return _is_collection_response(response.body)

    def _accept_existing(self, url: str, digest: str, expected_size: int) -> None:
        if not self._existing_matches(url, digest, expected_size):
            raise WebDavError("remote file differs from the intended upload")

    def _existing_matches(self, url: str, digest: str, expected_size: int) -> bool:
        response = self._request(
            "GET", url, retry=True, readiness=True, max_body=expected_size + 1
        )
        if response.status != 200:
            raise WebDavError(f"WebDAV verification failed with HTTP {response.status}")
        if len(response.body) != expected_size:
            return False
        return hashlib.sha256(response.body).hexdigest() == digest

    def _url(self, relative_path: str, *, collection: bool = False) -> str:
        encoded = "/".join(
            urllib.parse.quote(segment, safe="") for segment in _path_segments(relative_path)
        )
        url = urllib.parse.urljoin(self.base_url, encoded)
        if collection:
            url += "/"
        return url

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        retry: bool,
        readiness: bool = False,
        max_body: int = 64 * 1024,
    ) -> _Response:
        if readiness and method not in ("GET", "HEAD"):
            raise ValueError("readiness retries are limited to GET and HEAD")
        transient_attempts = self.retries if retry else 0
        readiness_deadline = self._monotonic() + self.readiness_timeout
        readiness_attempt = 0

        def pause(delay: float) -> None:
            if readiness:
                delay = min(delay, max(0, readiness_deadline - self._monotonic()))
            self._sleep(delay)

        request_headers = {
            "Authorization": self._authorization,
            "User-Agent": "mail-attachment-importer/1",
        }
        if headers:
            request_headers.update(headers)
        while True:
            request_timeout = self.timeout
            if readiness:
                remaining = readiness_deadline - self._monotonic()
                if remaining <= 0:
                    raise WebDavError("WebDAV readiness wait expired")
                request_timeout = min(request_timeout, remaining)
            request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
            try:
                with self._opener.open(request, timeout=request_timeout) as response:
                    response_body = response.read(max_body + 1)
                    if len(response_body) > max_body:
                        raise WebDavError("WebDAV response exceeded the allowed size")
                    result = _Response(response.status, response_body, dict(response.headers))
            except urllib.error.HTTPError as error:
                try:
                    if 300 <= error.code < 400:
                        raise WebDavError("WebDAV redirects are refused") from None
                    response_body = error.read(max_body + 1)
                    if len(response_body) > max_body:
                        raise WebDavError("WebDAV response exceeded the allowed size")
                    result = _Response(error.code, response_body, dict(error.headers))
                finally:
                    error.close()
            except (TimeoutError, OSError):
                if transient_attempts == 0:
                    raise
                transient_attempts -= 1
                pause(min(0.25 * (2 ** (self.retries - transient_attempts - 1)), 1.0))
                continue
            if result.status in (429, 502, 503, 504) and transient_attempts:
                transient_attempts -= 1
                pause(min(0.25 * (2 ** (self.retries - transient_attempts - 1)), 1.0))
                continue
            if result.status == 425 and readiness:
                remaining = readiness_deadline - self._monotonic()
                if remaining <= 0:
                    return result
                fallback = min(2**readiness_attempt, 10)
                hinted = _retry_after_seconds(result.headers)
                delay = min(max(fallback, hinted if hinted is not None else 0), remaining)
                self._sleep(delay)
                readiness_attempt += 1
                if delay == remaining:
                    return result
                continue
            if result.status in (401, 403):
                raise WebDavAuthenticationError(
                    f"WebDAV authentication failed with HTTP {result.status}"
                )
            return result


def _is_collection_response(body: bytes) -> bool:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return False
    for propstat in root.findall(".//{DAV:}propstat"):
        status = propstat.findtext("{DAV:}status", "")
        resource_type = propstat.find("{DAV:}prop/{DAV:}resourcetype")
        if " 200 " in status and resource_type is not None:
            if resource_type.find("{DAV:}collection") is not None:
                return True
    return False


def _path_segments(relative_path: str) -> tuple[str, ...]:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or relative_path.startswith(("/", "\\"))
    ):
        raise WebDavError("WebDAV path must be relative")
    segments = tuple(relative_path.split("/"))
    if any(
        not segment
        or segment in (".", "..")
        or "\\" in segment
        or any(ord(character) < 32 or ord(character) == 127 for character in segment)
        for segment in segments
    ):
        raise WebDavError("WebDAV path contains an unsafe segment")
    return segments


def _temporary_path(relative_path: str, digest: str) -> str:
    segments = _path_segments(relative_path)
    return "/".join((*segments[:-1], f".mail-attachment-importer-{digest}.tmp"))


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = next(
        (header_value for name, header_value in headers.items() if name.casefold() == "retry-after"),
        None,
    )
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                return None
            seconds = max(0, int(retry_at.timestamp() - time.time()))
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        return float(max(0, seconds))
    except OverflowError:
        return None
