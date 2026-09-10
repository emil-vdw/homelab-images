import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from common import Client, NoRedirect, ToolError, credential, instant
from vikunja import Vikunja
from radicale_tool import Radicale, event_data, events, properties


def args(**changes):
    values = dict(uid='test-123', title='Family, meal; café\nBring snacks',
                  start='2026-10-25T02:30:00+02:00', end='2026-10-25T02:30:00+01:00',
                  description='A long description ' + 'é' * 200, location='Home',
                  all_day=False, alarm_minutes=15, dry_run=False,
                  calendar='https://example.test/caldav/user/family/',
                  project_id=42, due='2026-10-25T02:30:00+02:00',
                  remind=['2026-10-24T15:00:00+02:00'], priority=2)
    values.update(changes)
    return argparse.Namespace(**values)


class ToolsTest(unittest.TestCase):
    def test_timestamp_requires_offset(self):
        for value in ('2026-10-25', '2026-10-25T02:30:00', 'bad'):
            with self.assertRaises(ToolError):
                instant(value)
        self.assertEqual(instant('2026-10-25T02:30:00+02:00').hour, 0)

    def test_ical_roundtrip_folding_escaping_and_dst(self):
        data = event_data(args())
        self.assertTrue(all(len(line) <= 75 for line in data.split(b'\r\n')))
        item = events(data)[0]
        self.assertEqual(item['summary'], args().title)
        self.assertEqual(item['description'], args().description)
        self.assertEqual(item['dtstart'], '2026-10-25T00:30:00+00:00')
        self.assertEqual(item['dtend'], '2026-10-25T01:30:00+00:00')
        self.assertIn('TRIGGER:-PT15M', item['alarms'][0])

    def test_all_day_exclusive_end(self):
        item = events(event_data(args(all_day=True, start='2026-09-12', end='2026-09-13')))[0]
        self.assertEqual(item['dtstart'], '2026-09-12')
        self.assertEqual(item['dtend'], '2026-09-13')
        with self.assertRaises(ToolError):
            event_data(args(all_day=True, start='2026-09-12', end='2026-09-12'))

    def test_event_validation(self):
        for changes in ({'uid': '../escape'}, {'uid': 'bad\nUID'}, {'title': ' '},
                        {'end': '2026-01-01T00:00:00Z'}):
            with self.assertRaises(ToolError):
                event_data(args(**changes))

    def test_url_scope_and_tls(self):
        client = Client('https://example.test/caldav/', 'secret')
        for url in ('http://example.test/caldav/', 'https://evil.test/caldav/', '/outside/',
                    '/caldav/%2e%2e/outside', '/caldav/a%5cb', '//evil.test/x'):
            with self.assertRaises(ToolError):
                client.url(url)
        self.assertEqual(client.url('/caldav/user/'), 'https://example.test/caldav/user/')
        with self.assertRaises(ToolError):
            Client('http://example.test/', 'secret')
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test/'))

    def test_credentials_private_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'
            path.write_text(json.dumps({'token': 'private-test-token'}))
            with patch.dict(os.environ, VIKUNJA_CREDENTIALS_FILE=str(path)):
                path.chmod(0o644)
                with self.assertRaises(ToolError):
                    credential('vikunja')
                path.chmod(0o600)
                self.assertEqual(credential('vikunja')['token'], 'private-test-token')
                path.write_text('invalid private-test-token')
                with self.assertRaises(ToolError) as raised:
                    credential('vikunja')
                self.assertNotIn('private-test-token', str(raised.exception))

    def test_transport_errors_no_retry_no_secret(self):
        client = Client('https://example.test/api/v1/', 'private-test-token')
        for error in (HTTPError(client.base, 401, 'private-test-token', {}, None),
                      URLError('private-test-token')):
            with patch.object(client.opener, 'open', side_effect=error) as request:
                with self.assertRaises(ToolError) as raised:
                    client.request('PUT', 'tasks', b'{}')
                self.assertNotIn('private-test-token', str(raised.exception))
                self.assertEqual(request.call_count, 1)

    def test_project_pagination_respects_server_page_cap(self):
        client = Vikunja('https://example.test/api/v1/', 'secret')
        with patch.object(client, 'api', side_effect=[([{'id': 1}], {}), ([{'id': 2}], {}), ([], {})]) as api:
            self.assertEqual(len(client.listing('projects', s='Family & home')), 2)
            self.assertIn('page=2', api.call_args_list[1].args[1])
            self.assertIn('s=Family+%26+home', api.call_args_list[0].args[1])

    def test_task_creation_reminders_and_readback(self):
        client = Vikunja('https://example.test/api/v1/', 'secret')
        with patch.object(client, 'api', side_effect=[({'id': 7}, {}), ({'id': 7, 'title': args().title}, {})]) as api:
            result = client.create(args())
            self.assertTrue(result['verified'])
            method, path, payload = api.call_args_list[0].args
            self.assertEqual((method, path), ('PUT', 'projects/42/tasks'))
            self.assertEqual(payload['due_date'], '2026-10-25T00:30:00+00:00')
            self.assertEqual(payload['reminders'], [{'reminder': '2026-10-24T13:00:00+00:00'}])

    def test_task_readback_failure_retains_id(self):
        client = Vikunja('https://example.test/api/v1/', 'secret')
        with patch.object(client, 'api', side_effect=[({'id': 7}, {}), ToolError('failed')]) as api:
            result = client.create(args())
            self.assertTrue(result['created'])
            self.assertFalse(result['verified'])
            self.assertEqual(result['id'], 7)
            self.assertEqual(api.call_count, 2)

    def test_dry_runs_do_not_write(self):
        for client in (Vikunja('https://example.test/api/v1/', 'secret'),
                       Radicale('https://example.test/caldav/', 'secret')):
            with patch.object(client, 'request') as request:
                self.assertTrue(client.create(args(dry_run=True))['dry_run'])
                request.assert_not_called()

    def test_caldav_create_is_conditional_and_verified(self):
        client = Radicale('https://example.test/caldav/', 'secret')
        with patch.object(client, 'calendar', return_value=args().calendar), patch.object(client, 'request', side_effect=[(b'', {}), (event_data(args()), {})]) as request:
            result = client.create(args())
            self.assertTrue(result['verified'])
            self.assertEqual(request.call_args_list[0].args[0], 'PUT')
            self.assertEqual(request.call_args_list[0].args[3]['If-None-Match'], '*')
            self.assertTrue(result['url'].endswith('/test-123.ics'))

    def test_caldav_conflict_not_overwritten(self):
        client = Radicale('https://example.test/caldav/', 'secret')
        with patch.object(client, 'calendar', return_value=args().calendar), patch.object(client, 'request', side_effect=ToolError('HTTP 412')) as request:
            with self.assertRaises(ToolError):
                client.create(args())
            self.assertEqual(request.call_count, 1)

    def test_missing_calendar_fails_before_write(self):
        client = Radicale('https://example.test/caldav/', 'secret')
        with patch.object(client, 'discover', return_value=[]), patch.object(client, 'request') as request:
            with self.assertRaises(ToolError):
                client.create(args())
            request.assert_not_called()

    def test_propstat_ignores_failed_properties(self):
        data = b'''<D:multistatus xmlns:D="DAV:"><D:response><D:href>/caldav/u/</D:href>
        <D:propstat><D:prop><D:displayname>Good</D:displayname></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
        <D:propstat><D:prop><D:displayname>Bad</D:displayname></D:prop><D:status>HTTP/1.1 404 Not Found</D:status></D:propstat>
        </D:response></D:multistatus>'''
        self.assertEqual(list(properties(data))[0][1].findtext('{DAV:}displayname'), 'Good')


if __name__ == '__main__':
    unittest.main()
