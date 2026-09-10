"""Discover/search CalDAV calendars and create events through authenticated HTTPS."""
import argparse
import base64
from datetime import date, datetime, timedelta, timezone
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote
from icalendar import Calendar, Event, Alarm
from common import Client, ToolError, credential, instant, nonempty, positive, run

DAV = 'DAV:'
CAL = 'urn:ietf:params:xml:ns:caldav'
NS = {'d': DAV, 'c': CAL}


def properties(body):
    root = ET.fromstring(body)
    if root.tag != '{DAV:}multistatus':
        raise ToolError('Expected CalDAV multistatus response')
    for response in root.findall('d:response', NS):
        href = response.findtext('d:href', namespaces=NS)
        props = ET.Element('props')
        for propstat in response.findall('d:propstat', NS):
            if ' 200 ' in propstat.findtext('d:status', default='', namespaces=NS):
                prop = propstat.find('d:prop', NS)
                if prop is not None:
                    props.extend(prop)
        if href:
            yield href, props


def event_data(args):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', args.uid):
        raise ToolError('UID must contain 1–128 letters, digits, underscores or hyphens')
    try:
        start, end = (date.fromisoformat(args.start), date.fromisoformat(args.end)) if args.all_day else (instant(args.start), instant(args.end))
    except ValueError:
        raise ToolError('All-day dates must use YYYY-MM-DD') from None
    if end <= start:
        raise ToolError('End must be later than start; all-day end is exclusive')
    calendar = Calendar()
    calendar.add('prodid', '-//Homelab productivity tools//EN')
    calendar.add('version', '2.0')
    event = Event()
    event.add('uid', args.uid)
    event.add('dtstamp', datetime.now(timezone.utc))
    event.add('dtstart', start)
    event.add('dtend', end)
    event.add('summary', nonempty(args.title))
    event.add('description', args.description)
    event.add('location', args.location)
    if args.alarm_minutes is not None:
        alarm = Alarm()
        alarm.add('action', 'DISPLAY')
        alarm.add('description', args.title)
        alarm.add('trigger', -timedelta(minutes=args.alarm_minutes))
        event.add_component(alarm)
    calendar.add_component(event)
    return calendar.to_ical()


def events(body):
    result = []
    for event in Calendar.from_ical(body).walk('VEVENT'):
        item = {key: str(event.get(key, '')) for key in ('uid', 'summary', 'description', 'location')}
        for key in ('dtstart', 'dtend', 'recurrence-id'):
            value = event.get(key)
            if value:
                item[key] = value.dt.isoformat()
        item['alarms'] = [alarm.to_ical().decode() for alarm in event.subcomponents if alarm.name == 'VALARM']
        result.append(item)
    return result


class Radicale(Client):
    def propfind(self, href, names, depth='0'):
        root = ET.Element('{DAV:}propfind')
        prop = ET.SubElement(root, '{DAV:}prop')
        for name in names:
            ET.SubElement(prop, name)
        body, _ = self.request('PROPFIND', href, ET.tostring(root),
                              {'Depth': depth, 'Content-Type': 'application/xml; charset=utf-8'})
        return list(properties(body))

    def discover(self):
        principal = self.propfind('', ['{DAV:}current-user-principal'])
        principals = [p.findtext('d:current-user-principal/d:href', namespaces=NS) for _, p in principal]
        principals = [p for p in principals if p]
        if len(principals) != 1:
            raise ToolError('Could not discover an authenticated principal')
        home = self.propfind(principals[0], ['{' + CAL + '}calendar-home-set'])
        homes = [p.findtext('c:calendar-home-set/d:href', namespaces=NS) for _, p in home]
        homes = [h for h in homes if h]
        if len(homes) != 1:
            raise ToolError('Could not discover calendar home')
        result = []
        for href, prop in self.propfind(homes[0], ['{DAV:}displayname', '{DAV:}resourcetype', '{' + CAL + '}supported-calendar-component-set'], '1'):
            if prop.find('d:resourcetype/c:calendar', NS) is None:
                continue
            components = [c.get('name') for c in prop.findall('c:supported-calendar-component-set/c:comp', NS)]
            if components and 'VEVENT' not in components:
                continue
            result.append({'url': self.url(href), 'name': prop.findtext('d:displayname', default='', namespaces=NS)})
        return result

    def calendar(self, url):
        url = self.url(url)
        if not url.endswith('/') or url not in {c['url'] for c in self.discover()}:
            raise ToolError('Choose an exact calendar URL returned by calendars')
        return url

    def search(self, args):
        start, end = instant(args.start), instant(args.end)
        if end <= start:
            raise ToolError('Search end must be later than start')
        calendar = self.calendar(args.calendar)
        bounds = {'start': start.strftime('%Y%m%dT%H%M%SZ'), 'end': end.strftime('%Y%m%dT%H%M%SZ')}
        root = ET.Element('{' + CAL + '}calendar-query')
        prop = ET.SubElement(root, '{DAV:}prop')
        ET.SubElement(prop, '{DAV:}getetag')
        data = ET.SubElement(prop, '{' + CAL + '}calendar-data')
        ET.SubElement(data, '{' + CAL + '}expand', bounds)
        filt = ET.SubElement(root, '{' + CAL + '}filter')
        vc = ET.SubElement(filt, '{' + CAL + '}comp-filter', {'name': 'VCALENDAR'})
        ve = ET.SubElement(vc, '{' + CAL + '}comp-filter', {'name': 'VEVENT'})
        ET.SubElement(ve, '{' + CAL + '}time-range', bounds)
        body, _ = self.request('REPORT', calendar, ET.tostring(root),
                              {'Depth': '1', 'Content-Type': 'application/xml; charset=utf-8'})
        result = []
        for href, props in properties(body):
            data = props.findtext('c:calendar-data', namespaces=NS)
            if data:
                for item in events(data):
                    if args.search.casefold() in ' '.join(item.get(k, '') for k in ('summary', 'description', 'location')).casefold():
                        result.append(dict(item, url=self.url(href), etag=props.findtext('d:getetag', namespaces=NS)))
        return result

    def create(self, args):
        data = event_data(args)
        if args.dry_run:
            return {'dry_run': True, 'calendar': self.url(args.calendar), 'icalendar': data.decode()}
        calendar = self.calendar(args.calendar)
        url = calendar + quote(args.uid, safe='') + '.ics'
        self.request('PUT', url, data, {'Content-Type': 'text/calendar; charset=utf-8', 'If-None-Match': '*'})
        result = {'created': True, 'uid': args.uid, 'url': url}
        try:
            body, _ = self.request('GET', url)
            saved = events(body)
            result.update(verified=any(e['uid'] == args.uid for e in saved), events=saved)
        except (ToolError, ValueError):
            result['verified'] = False
        if not result['verified']:
            result['warning'] = 'Created, but verification failed. Read this URL; do not use a new UID.'
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('calendars', help='Discover calendars, including accepted mapped shares')
    search = sub.add_parser('events', help='Search a time range, expanding recurring events')
    search.add_argument('--calendar', required=True)
    search.add_argument('--start', required=True)
    search.add_argument('--end', required=True)
    search.add_argument('--search', default='')
    get = sub.add_parser('get', help='Read an event by its returned URL')
    get.add_argument('url')
    create = sub.add_parser('create', help='Create a new event without overwriting an existing resource')
    create.add_argument('--calendar', required=True)
    create.add_argument('--uid', required=True, help='Generate a UUID once and retain it for this request')
    create.add_argument('--title', required=True)
    create.add_argument('--start', required=True, help='ISO timestamp with UTC offset, or all-day date')
    create.add_argument('--end', required=True, help='Exclusive end')
    create.add_argument('--all-day', action='store_true')
    create.add_argument('--description', default='')
    create.add_argument('--location', default='')
    create.add_argument('--alarm-minutes', type=positive)
    create.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    auth = credential('radicale')
    if ':' in auth['username']:
        raise ToolError('Username must not contain a colon')
    basic = base64.b64encode((auth['username'] + ':' + auth['token']).encode()).decode()
    client = Radicale(os.environ.get('RADICALE_URL', ''), 'Basic ' + basic)
    if args.command == 'calendars':
        return client.discover()
    if args.command == 'events':
        return client.search(args)
    if args.command == 'get':
        return events(client.request('GET', args.url)[0])
    return client.create(args)


if __name__ == '__main__':
    run(main)
