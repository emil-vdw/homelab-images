# Codex remote host

A single non-root development host for the experimental ChatGPT mobile Remote
workflow. Includes Git/GitHub CLI, SSH, Node/npm, Python/venv, a C/C++ toolchain,
and the official managed standalone Codex distribution. No inbound port is
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
