#!/usr/bin/env python3
"""Credential-free integration checks against a real HAProxy process."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get('MAIL_RUNTIME', HERE / 'runtime.py'))
spec = importlib.util.spec_from_file_location('mail_runtime', RUNTIME)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def certificate(directory, name, hostname):
    cert, key = directory / f'{name}.crt', directory / f'{name}.key'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-days', '2', '-keyout', str(key), '-out', str(cert),
                    '-subj', f'/CN={hostname}', '-addext', f'subjectAltName=DNS:{hostname},IP:127.0.0.1'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert, key


def eventually(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, ssl.SSLError):
            pass
        time.sleep(0.1)
    raise AssertionError('Condition did not become true')


class Challenge(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'challenge-response')

    def log_message(self, *_args):
        pass


class RuntimeTests(unittest.TestCase):
    def test_invalid_allowlist_rejected(self):
        with self.assertRaises(ValueError):
            runtime.networks('192.168.25.0/24,not-a-network')

    def test_exclusive_vault_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            original = runtime.RUN
            runtime.RUN = Path(temp)
            try:
                with runtime.instance_lock():
                    with self.assertRaises(SystemExit):
                        runtime.instance_lock()
                with runtime.instance_lock():
                    pass
            finally:
                runtime.RUN = original

    def test_proxy_bootstrap_tls_acl_and_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tls, public = root / 'tls', root / 'public'
            tls.mkdir()
            public.mkdir()
            ca, key = certificate(root, 'backend', '127.0.0.1')
            (public / 'bridge-ca.pem').write_bytes(ca.read_bytes())
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(ca, key)
            listeners = []
            done = threading.Event()

            def backend(listener, greeting):
                while not done.is_set():
                    try:
                        conn, _ = listener.accept()
                    except OSError:
                        return
                    try:
                        with context.wrap_socket(conn, server_side=True) as secure:
                            secure.sendall(greeting)
                    except (OSError, ssl.SSLError):
                        conn.close()

            for port, greeting in ((1143, b'* OK test IMAP\r\n'), (1025, b'220 test SMTP\r\n')):
                listener = socket.socket()
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(('127.0.0.1', port))
                listener.listen()
                listeners.append(listener)
                threading.Thread(target=backend, args=(listener, greeting), daemon=True).start()
            http_server = ThreadingHTTPServer(('127.0.0.1', 0), Challenge)
            threading.Thread(target=http_server.serve_forever, daemon=True).start()
            env = dict(os.environ, BRIDGE_MODE='run', PROXY_RUN=str(root / 'run'), TLS_DIR=str(tls),
                       BRIDGE_PUBLIC=str(public), TRUSTED_CIDRS='127.0.0.1/32', ACME_CIDRS='127.0.0.1/32',
                       ACME_UPSTREAM=f'127.0.0.1:{http_server.server_port}')
            log = (root / 'proxy.log').open('w+')
            process = subprocess.Popen([sys.executable, str(RUNTIME), 'proxy'], env=env, stdout=log, stderr=log)

            def request(path='/.well-known/acme-challenge/test', host='mail.terminus.home.arpa', method='GET'):
                conn = http.client.HTTPConnection('127.0.0.1', 10080, timeout=2)
                try:
                    conn.request(method, path, headers={'Host': host})
                    result = conn.getresponse()
                    return result.status, result.read()
                finally:
                    conn.close()

            def greeting(port, cert):
                context = ssl.create_default_context(cafile=str(cert))
                with socket.create_connection(('127.0.0.1', port), timeout=3) as conn:
                    with context.wrap_socket(conn, server_hostname='mail.terminus.home.arpa') as secure:
                        return secure.recv(100)

            try:
                eventually(lambda: request() == (200, b'challenge-response'))
                self.assertEqual(request('/')[0], 403)
                self.assertEqual(request(host='another.terminus.home.arpa')[0], 403)
                self.assertEqual(request(method='POST')[0], 403)
                self.assertEqual(request('/.well-known/acme-challenge/../secret')[0], 403)
                for port in (1993, 1465):
                    with self.assertRaises(OSError):
                        socket.create_connection(('127.0.0.1', port), timeout=1)
                frontend, frontend_key = certificate(root, 'frontend', 'mail.terminus.home.arpa')
                (tls / 'tls.key').write_bytes(frontend_key.read_bytes())
                (tls / 'tls.crt').write_bytes(frontend.read_bytes())
                eventually(lambda: greeting(1993, frontend).startswith(b'* OK'))
                self.assertTrue(greeting(1465, frontend).startswith(b'220'))
                # A different source IP must be rejected before TLS authentication.
                conn = socket.socket()
                conn.settimeout(3)
                conn.bind(('127.0.0.2', 0))
                conn.connect(('127.0.0.1', 1993))
                with self.assertRaises((OSError, ssl.SSLError)):
                    ssl.create_default_context(cafile=str(frontend)).wrap_socket(conn, server_hostname='mail.terminus.home.arpa')
                conn.close()
                replacement, replacement_key = certificate(root, 'replacement', 'mail.terminus.home.arpa')
                (tls / 'tls.key').write_bytes(replacement_key.read_bytes())
                (tls / 'tls.crt').write_bytes(replacement.read_bytes())
                eventually(lambda: greeting(1993, replacement).startswith(b'* OK'))
                # Losing the backend trust anchor closes mail but leaves ACME usable.
                (public / 'bridge-ca.pem').unlink()

                def closed():
                    try:
                        with socket.create_connection(('127.0.0.1', 1993), timeout=1):
                            return False
                    except OSError:
                        return True

                eventually(closed)
                self.assertEqual(request()[0], 200)
            finally:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                done.set()
                for listener in listeners:
                    listener.close()
                http_server.shutdown()
                http_server.server_close()
                log.seek(0)
                output = log.read()
                log.close()
                if process.returncode != 0:
                    self.fail(output)


if __name__ == '__main__':
    unittest.main()
