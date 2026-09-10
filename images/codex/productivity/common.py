"""Shared transport and input validation for the productivity CLIs."""
import json
import os
from pathlib import Path
import ssl
import stat
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import build_opener, HTTPSHandler, HTTPRedirectHandler, Request
from datetime import datetime, timezone


class ToolError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def credential(service):
    path = Path(os.environ.get(service.upper() + '_CREDENTIALS_FILE',
                              str(Path.home() / '.config/homelab-tools' / (service + '.json'))))
    try:
        with path.open() as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ToolError('Credential file must be a regular file with mode 0600 or 0400')
            data = json.load(stream)
    except (OSError, ValueError):
        raise ToolError('Configure a private ' + service + ' credential JSON file; see the tool README') from None
    keys = ['token'] if service == 'vikunja' else ['username', 'token']
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k].strip() for k in keys):
        raise ToolError('Credential file is missing required fields')
    if any('\n' in data[k] or '\r' in data[k] for k in keys):
        raise ToolError('Credential fields must not contain line breaks')
    return data


def instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ToolError('Use an ISO 8601 timestamp with an explicit UTC offset or Z') from None


def positive(value):
    import argparse
    try:
        result = int(value)
        if result <= 0:
            raise ValueError()
        return result
    except ValueError:
        raise argparse.ArgumentTypeError('must be a positive integer') from None


def nonempty(value):
    if not value.strip():
        raise ToolError('Title must not be empty')
    return value


class Client:
    def __init__(self, base, authorization):
        self.base = base.rstrip('/') + '/'
        parsed = urlsplit(self.base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ToolError('Set the service URL to HTTPS without credentials, query or fragment')
        self.authorization = authorization
        context = ssl.create_default_context()
        ca = os.environ.get('HOMELAB_TOOLS_CA_FILE')
        if ca:
            context.load_verify_locations(cafile=ca)
        self.opener = build_opener(HTTPSHandler(context=context), NoRedirect())

    def url(self, href):
        result = urljoin(self.base, href)
        parsed, base = urlsplit(result), urlsplit(self.base)
        path = unquote(parsed.path)
        if ((parsed.scheme, parsed.netloc) != (base.scheme, base.netloc)
                or parsed.fragment or '%25' in parsed.path.lower() or '\\' in path or any(c in path for c in '\r\n')
                or any(p in ('.', '..') for p in path.split('/'))
                or not path.startswith(unquote(base.path))):
            raise ToolError('Refusing URL outside the configured service path')
        return result

    def request(self, method, href='', body=None, headers=None):
        headers = dict(headers or {})
        headers['Authorization'] = self.authorization
        request = Request(self.url(href), data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024:
                    raise ToolError('Response exceeded 16 MiB; narrow the search')
                return data, response.headers
        except HTTPError as error:
            # Error bodies/redirect locations may echo credentials or private content.
            raise ToolError(f'HTTP {error.code}; check permissions, destination and credentials. '
                            'Writes are not retried; search before repeating a create.') from None
        except (URLError, TimeoutError, OSError):
            raise ToolError('Connection or TLS failure. Write outcome may be unknown; '
                            'search before repeating a create.') from None


def run(main):
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except (ToolError, ValueError, OSError, ET.ParseError) as error:
        import sys
        # Only controlled errors include details; library errors may contain secrets.
        message = str(error) if isinstance(error, ToolError) else 'Invalid response, configuration or input'
        print(json.dumps({'error': message}), file=sys.stderr)
        raise SystemExit(1)
