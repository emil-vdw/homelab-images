"""Disposable Vikunja integration; set VIKUNJA_TEST_BINARY to a verified 2.6.0 binary."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'productivity'))
from vikunja import Vikunja
from common import ToolError
from test_tools import args


@unittest.skipUnless(os.environ.get('VIKUNJA_TEST_BINARY'), 'requires verified Vikunja binary')
class VikunjaIntegration(unittest.TestCase):
    def test_scoped_token_project_search_create_reminders_and_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, VIKUNJA_SERVICE_INTERFACE=f'127.0.0.1:{port}',
                VIKUNJA_SERVICE_ROOTPATH=directory, VIKUNJA_CORS_ENABLE='false', VIKUNJA_DATABASE_TYPE='sqlite',
                VIKUNJA_DATABASE_PATH=str(root/'db.sqlite'), VIKUNJA_FILES_BASEPATH=str(root/'files'),
                VIKUNJA_SERVICE_ENABLEREGISTRATION='true', VIKUNJA_SERVICE_ENABLEEMAILREMINDERS='false',
                VIKUNJA_LOG_LEVEL='ERROR', VIKUNJA_LOG_HTTP='off', VIKUNJA_SERVICE_MAXITEMSPERPAGE='1')
            with (root/'server.log').open('w') as log:
                process = subprocess.Popen([os.environ['VIKUNJA_TEST_BINARY']], cwd=directory, env=env, stdout=log, stderr=log)
                try:
                    for _ in range(100):
                        try:
                            with urlopen(f'http://127.0.0.1:{port}/health', timeout=1):
                                break
                        except OSError:
                            time.sleep(.1)
                    else:
                        self.fail('Disposable Vikunja did not start: ' + (root/'server.log').read_text())
                    def setup(path, payload, token=None, method='POST'):
                        headers = {'Content-Type': 'application/json'}
                        if token:
                            headers['Authorization'] = 'Bearer ' + token
                        with urlopen(Request(f'http://127.0.0.1:{port}/api/v1/{path}',
                                             json.dumps(payload).encode(), headers, method=method)) as response:
                            return json.load(response)
                    setup('register', {'username': 'tester', 'password': 'disposable-password-123', 'email': 'test@example.com'})
                    login = setup('login', {'username': 'tester', 'password': 'disposable-password-123'})['token']
                    project = setup('projects', {'title': 'Household'}, login, 'PUT')
                    setup('projects', {'title': 'Other'}, login, 'PUT')
                    token = setup('tokens', {'title': 'Test scoped agent token', 'expires_at': '2099-01-01T00:00:00Z',
                        'permissions': {'projects': ['read_all'], 'tasks': ['read_all', 'read_one', 'create'], 'tasks_comments': ['read_all']}}, login, 'PUT')['token']
                    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost', '-keyout', str(root/'key.pem'),
                        '-out', str(root/'cert.pem')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    class Proxy(BaseHTTPRequestHandler):
                        def log_message(self, *args):
                            pass
                        def forward(self):
                            connection = http.client.HTTPConnection('127.0.0.1', port)
                            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                            connection.request(self.command, self.path, body, dict(self.headers))
                            response = connection.getresponse()
                            data = response.read()
                            self.send_response(response.status)
                            for key, value in response.getheaders():
                                if key.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                                    self.send_header(key, value)
                            self.send_header('Content-Length', str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            connection.close()
                        do_GET = do_PUT = forward
                    server = HTTPServer(('127.0.0.1', 0), Proxy)
                    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    tls.load_cert_chain(root/'cert.pem', root/'key.pem')
                    server.socket = tls.wrap_socket(server.socket, server_side=True)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        with patch.dict(os.environ, HOMELAB_TOOLS_CA_FILE=str(root/'cert.pem')):
                            client = Vikunja(f'https://localhost:{server.server_port}/api/v1/', 'Bearer ' + token)
                            self.assertGreaterEqual(len(client.listing('projects')), 2)
                            result = client.create(args(project_id=project['id']))
                            self.assertTrue(result['verified'])
                            task = result['task']
                            self.assertEqual(task['project_id'], project['id'])
                            self.assertEqual(task['due_date'], '2026-10-25T00:30:00Z')
                            self.assertEqual(task['reminders'][0]['reminder'], '2026-10-24T13:00:00Z')
                            found = client.listing('tasks', s='Family', filter=f"project_id = {project['id']}")
                            self.assertEqual([t['id'] for t in found], [task['id']])
                            self.assertEqual(client.api('GET', f"tasks/{task['id']}?expand=comments")[0]['id'], task['id'])
                            with self.assertRaises(ToolError):
                                client.api('PUT', 'projects', {'title': 'Not authorized'})
                    finally:
                        server.shutdown()
                        server.server_close()
                        thread.join()
                finally:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
