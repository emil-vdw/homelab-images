import hashlib
import io
import ssl
import unittest
import urllib.request
import urllib.response
from unittest import mock

from mail_importer.webdav import WebDavClient, WebDavError, _NoRedirect


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def read(self, amount):
        return self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class FakeWebDav:
    def __init__(self):
        self.collections = set()
        self.files = {}
        self.requests = []
        self.timeout_after_put = False
        self.timeout_after_move = False
        self.destination_before_move = None
        self.transient_head_failures = 0

    def open(self, request, timeout):
        method = request.get_method()
        self.requests.append(request)
        url = request.full_url
        if method == "MKCOL":
            if url in self.collections:
                return FakeResponse(405)
            self.collections.add(url)
            return FakeResponse(201)
        if method == "PROPFIND":
            if url not in self.collections:
                return FakeResponse(404)
            return FakeResponse(
                207,
                b'<d:multistatus xmlns:d="DAV:"><d:response><d:propstat>'
                b"<d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>"
                b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                b"</d:multistatus>",
            )
        if method == "HEAD":
            if self.transient_head_failures:
                self.transient_head_failures -= 1
                return FakeResponse(503)
            return FakeResponse(200 if url in self.files else 404)
        if method == "GET":
            return FakeResponse(200, self.files[url]) if url in self.files else FakeResponse(404)
        if method == "PUT":
            self.files[url] = request.data
            if self.timeout_after_put:
                self.timeout_after_put = False
                raise TimeoutError
            return FakeResponse(201)
        if method == "MOVE":
            destination = request.headers["Destination"]
            if self.destination_before_move is not None:
                self.files[destination] = self.destination_before_move
                self.destination_before_move = None
            if destination in self.files:
                return FakeResponse(412)
            self.files[destination] = self.files.pop(url)
            if self.timeout_after_move:
                self.timeout_after_move = False
                raise TimeoutError
            return FakeResponse(201)
        if method == "DELETE":
            if url not in self.files:
                return FakeResponse(404)
            del self.files[url]
            return FakeResponse(204)
        raise AssertionError(method)


class RedirectingHttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        self.requests = []

    def https_open(self, request):
        self.requests.append(request)
        if len(self.requests) > 1:
            raise AssertionError("redirect was followed")
        response = urllib.response.addinfourl(
            io.BytesIO(b""),
            {"Location": "https://attacker.example/stolen"},
            request.full_url,
            302,
        )
        response.msg = "Found"
        return response


class WebDavTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeWebDav()
        self.client = WebDavClient(
            "https://cloud.example/remote.php/dav/spaces/a/",
            "user",
            "token",
            opener=self.server,
            retries=0,
        )

    def test_creates_each_encoded_collection_segment(self):
        self.client.ensure_collection("Home/Utilities and internet/Invoices")
        urls = [request.full_url for request in self.server.requests if request.method == "MKCOL"]
        self.assertEqual(
            urls,
            [
                "https://cloud.example/remote.php/dav/spaces/a/Home/",
                "https://cloud.example/remote.php/dav/spaces/a/Home/Utilities%20and%20internet/",
                "https://cloud.example/remote.php/dav/spaces/a/Home/Utilities%20and%20internet/Invoices/",
            ],
        )
        self.client.ensure_collection("Home/Utilities and internet/Invoices")

    def test_create_only_upload_and_verified_repeat(self):
        content = b"%PDF-1.7\ninvoice"
        digest = hashlib.sha256(content).hexdigest()
        self.client.upload_create_only("Invoices/a b.pdf", content, digest)
        self.client.upload_create_only("Invoices/a b.pdf", content, digest)
        puts = [request for request in self.server.requests if request.method == "PUT"]
        self.assertEqual(len(puts), 1)
        self.assertIn(".mail-attachment-importer-", puts[0].full_url)
        self.assertTrue(puts[0].headers["Oc-checksum"].startswith("SHA1:"))
        moves = [request for request in self.server.requests if request.method == "MOVE"]
        self.assertEqual(len(moves), 1)
        self.assertIn(".mail-attachment-importer-", moves[0].full_url)
        self.assertEqual(moves[0].headers["Overwrite"], "F")
        self.assertEqual(
            moves[0].headers["Destination"],
            "https://cloud.example/remote.php/dav/spaces/a/Invoices/a%20b.pdf",
        )
        final_url = "https://cloud.example/remote.php/dav/spaces/a/Invoices/a%20b.pdf"
        for request in self.server.requests:
            if request.method in ("PUT", "DELETE"):
                self.assertNotEqual(request.full_url, final_url)

    def test_timeout_after_server_acceptance_is_verified(self):
        content = b"%PDF-1.7\ninvoice"
        digest = hashlib.sha256(content).hexdigest()
        self.server.timeout_after_put = True
        self.client.upload_create_only("Invoices/file.pdf", content, digest)

    def test_timeout_after_move_is_verified(self):
        content = b"%PDF-1.7\ninvoice"
        digest = hashlib.sha256(content).hexdigest()
        self.server.timeout_after_move = True
        self.client.upload_create_only("Invoices/file.pdf", content, digest)
        self.assertIn(
            "https://cloud.example/remote.php/dav/spaces/a/Invoices/file.pdf",
            self.server.files,
        )

    def test_crash_left_temporary_file_is_reused(self):
        content = b"%PDF-1.7\ninvoice"
        digest = hashlib.sha256(content).hexdigest()
        temporary_url = (
            "https://cloud.example/remote.php/dav/spaces/a/Invoices/"
            f".mail-attachment-importer-{digest}.tmp"
        )
        self.server.files[temporary_url] = content
        self.client.upload_create_only("Invoices/file.pdf", content, digest)
        self.assertFalse(any(request.method == "PUT" for request in self.server.requests))
        self.assertNotIn(temporary_url, self.server.files)

    def test_partial_temporary_file_is_repaired(self):
        content = b"%PDF-1.7\ninvoice"
        digest = hashlib.sha256(content).hexdigest()
        temporary_url = (
            "https://cloud.example/remote.php/dav/spaces/a/Invoices/"
            f".mail-attachment-importer-{digest}.tmp"
        )
        self.server.files[temporary_url] = b"partial"
        self.client.upload_create_only("Invoices/file.pdf", content, digest)
        final_url = "https://cloud.example/remote.php/dav/spaces/a/Invoices/file.pdf"
        self.assertEqual(self.server.files[final_url], content)
        self.assertEqual(
            [request.full_url for request in self.server.requests if request.method == "PUT"],
            [temporary_url],
        )

    def test_verified_final_cleans_leftover_owned_temporary_file(self):
        content = b"%PDF-1.7\ninvoice"
        digest = hashlib.sha256(content).hexdigest()
        final_url = "https://cloud.example/remote.php/dav/spaces/a/Invoices/file.pdf"
        temporary_url = (
            "https://cloud.example/remote.php/dav/spaces/a/Invoices/"
            f".mail-attachment-importer-{digest}.tmp"
        )
        self.server.files[final_url] = content
        self.server.files[temporary_url] = content
        self.client.upload_create_only("Invoices/file.pdf", content, digest)
        self.assertEqual(self.server.files[final_url], content)
        self.assertNotIn(temporary_url, self.server.files)
        deletes = [request.full_url for request in self.server.requests if request.method == "DELETE"]
        self.assertEqual(deletes, [temporary_url])

    def test_move_conflict_verifies_winner_and_cleans_temporary_file(self):
        content = b"%PDF-1.7\ninvoice"
        digest = hashlib.sha256(content).hexdigest()
        self.server.destination_before_move = content
        self.client.upload_create_only("Invoices/file.pdf", content, digest)
        self.assertTrue(any(request.method == "DELETE" for request in self.server.requests))
        self.assertFalse(
            any(".mail-attachment-importer-" in url for url in self.server.files)
        )

    def test_transient_read_is_retried(self):
        content = b"%PDF-1.7\ninvoice"
        self.server.transient_head_failures = 1
        client = WebDavClient(
            "https://cloud.example/remote.php/dav/spaces/a/",
            "user",
            "token",
            opener=self.server,
            retries=1,
        )
        with mock.patch("mail_importer.webdav.time.sleep") as sleep:
            client.upload_create_only(
                "Invoices/file.pdf", content, hashlib.sha256(content).hexdigest()
            )
        sleep.assert_called_once()

    def test_tls_verification_failure_is_not_hidden(self):
        class RejectTls:
            def open(self, request, timeout):
                raise ssl.SSLCertVerificationError("synthetic TLS failure")

        client = WebDavClient(
            "https://cloud.example/root", "user", "token", opener=RejectTls(), retries=0
        )
        content = b"%PDF-1.7\ninvoice"
        with self.assertRaises(ssl.SSLCertVerificationError):
            client.upload_create_only(
                "Invoices/file.pdf", content, hashlib.sha256(content).hexdigest()
            )

    def test_existing_different_file_is_never_accepted(self):
        url = "https://cloud.example/remote.php/dav/spaces/a/Invoices/file.pdf"
        self.server.files[url] = b"other"
        content = b"%PDF-1.7\ninvoice"
        with self.assertRaisesRegex(WebDavError, "differs"):
            self.client.upload_create_only(
                "Invoices/file.pdf", content, hashlib.sha256(content).hexdigest()
            )
        self.assertEqual(self.server.files[url], b"other")

    def test_rejects_unsafe_paths_and_non_https_base(self):
        for path in ("/absolute.pdf", "../escape.pdf", "a//b.pdf", "a\\b.pdf"):
            with self.subTest(path=path), self.assertRaises(WebDavError):
                self.client.upload_create_only(path, b"x", hashlib.sha256(b"x").hexdigest())
        with self.assertRaises(WebDavError):
            WebDavClient("http://cloud.example/root", "user", "token")

    def test_auth_header_stays_on_base_origin(self):
        content = b"%PDF-1.7\ninvoice"
        self.client.upload_create_only(
            "Invoices/file.pdf", content, hashlib.sha256(content).hexdigest()
        )
        self.assertTrue(self.server.requests)
        for request in self.server.requests:
            self.assertEqual(request.host, "cloud.example")
            self.assertTrue(request.headers["Authorization"].startswith("Basic "))

    def test_redirect_is_refused_before_credentials_reach_another_origin(self):
        transport = RedirectingHttpsHandler()
        opener = urllib.request.build_opener(transport, _NoRedirect())
        client = WebDavClient(
            "https://cloud.example/root", "user", "token", opener=opener, retries=0
        )
        content = b"%PDF-1.7\ninvoice"
        with self.assertRaisesRegex(WebDavError, "redirects are refused"):
            client.upload_create_only(
                "Invoices/file.pdf", content, hashlib.sha256(content).hexdigest()
            )
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.requests[0].host, "cloud.example")


if __name__ == "__main__":
    unittest.main()
