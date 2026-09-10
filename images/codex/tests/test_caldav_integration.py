"""Run with a temporary venv containing icalendar and radicale==3.8.0.

Uses a disposable HTTPS WSGI server with the same /caldav prefix as a proxy.
No homelab credentials, network destinations or persistent calendars are used.
"""
import base64
import importlib.util
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

import radicale
from radicale import config

SOURCE = Path(__file__).resolve().parents[1] / 'productivity'
sys.path.insert(0, str(SOURCE))
from common import ToolError
from test_tools import args
spec = importlib.util.spec_from_file_location('calendar_tool', SOURCE / 'radicale_tool.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class CalDAVIntegration(unittest.TestCase):
    def test_discovery_create_search_conflict_and_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem')],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (root/'users').write_text('alice:disposable-password\n')
            configuration = config.load([])
            configuration.update({'auth': {'type': 'htpasswd', 'htpasswd_filename': str(root/'users'),
                'htpasswd_encryption': 'plain', 'delay': '0'},
                'storage': {'filesystem_folder': str(root/'data')}})
            application = radicale.Application(configuration)
            def proxy(environ, start_response):
                environ['SCRIPT_NAME'] = '/caldav'
                environ['PATH_INFO'] = environ['PATH_INFO'].removeprefix('/caldav')
                return application(environ, start_response)
            server = make_server('127.0.0.1', 0, proxy, handler_class=QuietHandler)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(root/'cert.pem', root/'key.pem')
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, HOMELAB_TOOLS_CA_FILE=str(root/'cert.pem')):
                    url = f'https://localhost:{server.server_port}/caldav/'
                    client = module.Radicale(url, 'Basic ' + base64.b64encode(b'alice:disposable-password').decode())
                    self.assertEqual(client.discover(), [])
                    client.request('MKCALENDAR', 'alice/family/', b'''<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:set><D:prop><D:displayname>Family</D:displayname><C:supported-calendar-component-set><C:comp name="VEVENT"/></C:supported-calendar-component-set></D:prop></D:set></C:mkcalendar>''', {'Content-Type': 'application/xml'})
                    calendars = client.discover()
                    self.assertEqual(calendars, [{'url': url + 'alice/family/', 'name': 'Family'}])
                    data = args(calendar=calendars[0]['url'])
                    self.assertTrue(client.create(data)['verified'])
                    with self.assertRaises(ToolError):
                        client.create(data)
                    found = client.search(args(calendar=data.calendar, start='2026-10-25T00:00:00Z', end='2026-10-26T00:00:00Z', search='café'))
                    self.assertEqual(len(found), 1)
                    self.assertEqual(found[0]['uid'], data.uid)
                    self.assertEqual(client.search(args(calendar=data.calendar, start='2027-01-01T00:00:00Z', end='2027-01-02T00:00:00Z', search='')), [])
                    all_day = args(calendar=data.calendar, uid='all-day', all_day=True, start='2026-09-12', end='2026-09-13')
                    self.assertTrue(client.create(all_day)['verified'])
                    series = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:series\r\nDTSTAMP:20260910T000000Z\r\nDTSTART:20261101T100000Z\r\nDTEND:20261101T110000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Recurring\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
                    client.request('PUT', data.calendar + 'series.ics', series, {'Content-Type': 'text/calendar', 'If-None-Match': '*'})
                    found = client.search(args(calendar=data.calendar, start='2026-11-01T00:00:00Z', end='2026-11-04T00:00:00Z', search='Recurring'))
                    self.assertEqual(len(found), 3)
                    with self.assertRaises(ToolError):
                        module.Radicale(url, 'Basic ' + base64.b64encode(b'alice:wrong-password').decode()).discover()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
