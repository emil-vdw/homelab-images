"""Vikunja v1 task discovery and creation (tested against 2.6.0)."""
import argparse
import json
import os
from urllib.parse import urlencode
from common import Client, ToolError, credential, instant, nonempty, positive, run


class Vikunja(Client):
    def api(self, method, path, data=None):
        body, headers = self.request(method, path,
            json.dumps(data).encode() if data is not None else None,
            {'Content-Type': 'application/json', 'Accept': 'application/json'})
        return json.loads(body), headers

    def listing(self, path, **query):
        items = []
        for page in range(1, 1001):
            batch, headers = self.api('GET', path + '?' + urlencode(dict(query, page=page, per_page=100)))
            if not isinstance(batch, list):
                raise ToolError('Expected a list from Vikunja')
            items.extend(batch)
            pages = headers.get('X-Pagination-Total-Pages')
            if not batch or (pages is not None and page >= int(pages)):
                return items
        raise ToolError('Pagination limit reached; narrow the search')

    def create(self, args):
        payload = {'title': nonempty(args.title), 'description': args.description, 'priority': args.priority}
        if args.due:
            payload['due_date'] = instant(args.due).isoformat()
        if args.remind:
            payload['reminders'] = [{'reminder': instant(value).isoformat()} for value in args.remind]
        if args.dry_run:
            return {'project_id': args.project_id, 'task': payload, 'dry_run': True}
        result, _ = self.api('PUT', f'projects/{args.project_id}/tasks', payload)
        task_id = result.get('id') if isinstance(result, dict) else None
        if not isinstance(task_id, int) or task_id <= 0:
            raise ToolError('Create response did not include an ID; search before repeating the write')
        output = {'created': True, 'id': task_id, 'url': self.base.split('/api/')[0] + f'/tasks/{task_id}', 'task': result}
        try:
            saved, _ = self.api('GET', f'tasks/{task_id}')
            output.update(verified=isinstance(saved, dict) and saved.get('id') == task_id, task=saved)
            if not output['verified']:
                output['warning'] = 'Created, but read-back ID differed. Inspect this ID before proceeding.'
        except (ToolError, ValueError):
            output.update(verified=False, warning='Created, but read-back failed. Read this ID; do not create again.')
        return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    projects = sub.add_parser('projects', help='List/search projects, including IDs and parent IDs')
    projects.add_argument('--search', default='')
    tasks = sub.add_parser('tasks', help='Search tasks across accessible projects')
    tasks.add_argument('--search', default='')
    tasks.add_argument('--project-id', type=positive)
    get = sub.add_parser('get', help='Read a task and its first 50 comments')
    get.add_argument('id', type=positive)
    create = sub.add_parser('create', help='Create one task in an explicit project')
    create.add_argument('--project-id', type=positive, required=True)
    create.add_argument('--title', required=True)
    create.add_argument('--description', default='')
    create.add_argument('--due', help='ISO timestamp with UTC offset')
    create.add_argument('--remind', action='append', default=[], help='Absolute reminder timestamp; repeatable')
    create.add_argument('--priority', type=int, choices=range(6), default=0)
    create.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    auth = credential('vikunja')
    client = Vikunja(os.environ.get('VIKUNJA_URL', ''), 'Bearer ' + auth['token'])
    if args.command == 'projects':
        return client.listing('projects', s=args.search)
    if args.command == 'tasks':
        query = {'s': args.search}
        if args.project_id:
            query['filter'] = f'project_id = {args.project_id}'
        return client.listing('tasks', **query)
    if args.command == 'get':
        return client.api('GET', f'tasks/{args.id}?expand=comments')[0]
    return client.create(args)


if __name__ == '__main__':
    run(main)
