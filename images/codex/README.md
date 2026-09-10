# Codex remote host

A single non-root development host for the experimental ChatGPT mobile Remote
workflow. Includes Git/GitHub CLI, SSH, Node/npm, Python/venv, a C/C++ toolchain,
kubectl, Flux CLI, Helm, and the official managed standalone Codex distribution. No inbound port is
exposed. UID/GID: `20213`.

Persist `/home/codex`, owned by `20213:20213` with mode `0700`. This preserves
Codex authentication, remote identity, conversations, GitHub login, and
`~/workspaces` across container replacement. Credentials remain writable so
OAuth refreshes can persist; do not inject a stale `auth.json` on every start.

The entrypoint enables remote control then starts the managed daemon. Tini
reaps detached children; the supervisor checks the real Unix-socket initialize
handshake and gracefully stops the daemon on termination. Container readiness
can use `codex app-server daemon version` (local host health, not relay/login
health). Logs and runtime state live under `~/.codex`.

## First login

Inside the running container, as the same user:

```sh
codex login --device-auth
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
codex remote-control pair
```

Complete the ChatGPT and GitHub device flows yourself, then enter the separate
pairing code in ChatGPT on your phone. ChatGPT may require device-code login to
be enabled in account security settings. Use the same ChatGPT account on both
ends. GitHub CLI stores credentials on the private home volume when a keyring
is unavailable; never print or commit them.

## Updates and configuration

Codex is pinned by `CODEX_VERSION`. The installer is SHA-256 checked and it
verifies the release downloads. Review both pins when upgrading. The managed
standalone path in the home volume links to the root-owned installation in
`/opt/codex`, so replacing the image selects the new executable even when the
home is restored from an older volume.

Cluster clients are pinned by `KUBECTL_VERSION`, `FLUX_VERSION`, and
`HELM_VERSION`, with upstream release checksums checked during the build.
Keep kubectl within one minor version of the target API server. Kubernetes
access is supplied by the deployment's service account and RBAC; the image
contains no kubeconfig, token, or cluster permissions.

Do not run `remote-control start`, `daemon bootstrap`, or `codex update` in this
image: the first two can start the standalone updater. Use
`codex app-server daemon restart` for a manual restart. Pin deployed image
digests; state migrations may require a volume snapshot to roll back.

User configuration is `~/.codex/config.toml`; personal instructions are
`~/.codex/AGENTS.md`. Mount authored skills at `/etc/codex/skills` or
`~/.agents/skills`. No skills or personal instructions are bundled here.

The default bubblewrap command sandbox needs user namespaces that restricted
container runtimes may block. Codex 0.153.4's deprecated Landlock fallback also
rejects modern workspace-write policies. Configure the command permissions for
your deployment deliberately: this image does not disable Codex's sandbox or
add pod privileges. Pairing alone does not establish that model commands work.

Run `bash images/codex/smoke-test.sh local/codex:ci` after building. It exercises
the real daemon, read-only image, crash recovery, home persistence and graceful
shutdown without credentials. Phone pairing, model turns, private repository
access, and reconnecting after a pod replacement still need an authenticated
end-to-end test. No Kubernetes credentials or Docker socket are included.

References: [daemon lifecycle](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/app-server-daemon/README.md),
[remote commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
[skills](https://learn.chatgpt.com/docs/build-skills).

## Productivity commands

`homelab-vikunja` searches projects/tasks, reads task details and creates tasks
with due dates and absolute reminders. `homelab-radicale` discovers CalDAV
calendars (including accepted shares), searches events in a time range, reads
individual events and creates timed/all-day events with optional display alarms.
Both emit JSON. See each command's `--help` and subcommand help.

These are Python CLIs invoked by the agent's shell tool. Python's standard
library handles HTTPS and CalDAV XML; Debian's `python3-icalendar` handles
calendar parsing, escaping and serialization. The host's global instructions
and deployment configuration live in the homelab repository.

Set `VIKUNJA_URL` (including `/api/v1/`) and `RADICALE_URL` (the authenticated
CalDAV root). Both must use HTTPS. `HOMELAB_TOOLS_CA_FILE` optionally adds a CA
bundle to system trust. URLs returned by discovery must remain on that origin
and within that root; redirects are rejected. CalDAV must be accessed through
its authenticating proxy, not a trusted-header backend.

Provision mode `0600` JSON files on the private home volume:

- `~/.config/homelab-tools/vikunja.json`: `{"token":"<API token>"}`
- `~/.config/homelab-tools/radicale.json`: `{"username":"<login>","token":"<app token>"}`

`VIKUNJA_CREDENTIALS_FILE` and `RADICALE_CREDENTIALS_FILE` override those paths.
Credentials are never supplied in CLI arguments. Missing credentials affect
only the tool invocation, not host startup. Give the Vikunja token project read
and task read/create plus task-comment read-all permissions; the tools expose no update/delete commands.

Creates require an explicit project ID/calendar URL. Event creation also needs
a stable `--uid` (generate a UUID once per intended event). It uses conditional
PUT to prevent overwriting an existing resource. Writes are not automatically
retried. A read-back failure preserves the successful creation's ID/URL; inspect
it before repeating a request. Vikunja creation has no client idempotency key.
`--dry-run` renders the payload without sending a request (configuration and
credentials must still be present). Timestamp arguments require UTC offsets;
all-day event end dates are exclusive. Notification delivery depends on clients.

Offline tests run during image builds:

```sh
python3 -m unittest discover -s images/codex/productivity -v
```

For disposable protocol integration tests, use a temporary venv with
`icalendar` and `radicale==3.8.0`. The tests use local TLS servers and generated
certificates; no production credentials or data are involved:

```sh
python images/codex/tests/test_caldav_integration.py
VIKUNJA_TEST_BINARY=/path/to/verified/vikunja-v2.6.0-linux-amd64 \
  python images/codex/tests/test_vikunja_integration.py
```

The Vikunja test requires an upstream binary verified against its release
checksum. It creates a disposable account, scoped API token and SQLite database.
Production network policy, proxy authentication and notification delivery need
an authenticated acceptance test after the deployment updates its image pin.

Protocol references: [Vikunja 2.6.0 schema](https://github.com/go-vikunja/vikunja/blob/v2.6.0/pkg/swagger/swagger.json),
[CalDAV](https://www.rfc-editor.org/rfc/rfc4791),
[OpenCloud app-token authentication](https://docs.opencloud.eu/docs/admin/configuration/radicale-integration/).
