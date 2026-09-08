# Proton Mail Bridge for Terminus

Packages the unmodified headless executable from Proton's official 3.26.0 amd64
Debian package. Both its SHA-256 and its embedded Debian signature are checked
against a checksum-pinned Proton public key and a local verification policy.
The GUI and self-updating launcher are omitted. `pass`, GPG, HAProxy, Python's
standard library, and tini provide the container lifecycle. No Proton account
or credentials are used at build time.

## Security review before publication

Review the image scan against the exact packaged binary and deployment mode.
Record each finding's affected function, input path, impact, mitigations and
remaining uncertainty in the image PR. Module presence alone does not establish
runtime reachability; private networking does not protect parsers from mail
received through Proton. Preserve the scan gate until fixes or narrowly scoped,
reviewed exceptions address the findings. Reassess exceptions on image upgrades.
Check the official release channel as well as version and signature provenance.
Keep the deployment suspended until the image is approved and its digest pinned.

The Kubernetes deployment uses two containers from this image:

- `bridge`: dedicated UID 20214, persistent `/data`, temporary `/run/bridge`,
  Secret `/keyring/{gpg-private.asc,fingerprint}`.
- `proxy`: UID 20215, group 20214, temporary `/run/proxy`, cert-manager Secret
  `/tls/{tls.crt,tls.key}`, and **only** `/data/public` mounted read-only as
  `/bridge-public`. Never mount the full Bridge data volume or keyring here.

`BRIDGE_MODE=setup` starts the supervisor and HTTP-01 forwarding only; no mail
listeners are opened. Run `mail-runtime cli` in a private exec terminal, export
Bridge's TLS certificate to `/data/tls-export`, exit, and run
`mail-runtime publish-cert`. Switch to `BRIDGE_MODE=run` with a Git-driven pod
recreation to start Bridge noninteractively and enable the proxy's mail TLS.
An exclusive lock prevents the CLI and daemon from using the vault together.
GPG key mismatch/decryption failure stops startup. Interactive account output
belongs to the administrator exec session, not the container's stdout logs.

See `homelab/docs/proton-mail-bridge.org` for the complete setup, SOPS, private
network, iOS, validation and recovery procedure. Native runtime state is the
source of truth for Proton's rotating tokens and generated client password;
Kubernetes Secrets are not a substitute for the persistent vault.

## Build and test

```sh
docker build --platform linux/amd64 -t local/proton-mail-bridge:ci images/proton-mail-bridge
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /run \
  --entrypoint python3 -v "$PWD/images/proton-mail-bridge:/tests:ro" \
  local/proton-mail-bridge:ci -m unittest discover -s /tests -v
```

Tests use real HAProxy and synthetic loopback TLS servers. They cover initial
issuance without mail TLS, challenge path/host/method restrictions, TLS on both
mail ports, source-IP rejection, certificate reload and fail-closed removal of
backend trust, plus exclusive state locking and keyring recovery after loss of its temporary GPG home. They do not establish Proton
account authentication, Cilium/MetalLB behavior, or iOS compatibility. Run the
homelab acceptance checks after deployment with a paid account.

The image pipeline publishes this image for `linux/amd64` only. Other existing
images retain both architectures. Pin the resulting release digest in homelab
before unsuspending the child Flux Kustomization. Disable Bridge autoupdates
during initial CLI setup. For upgrades update package version/hash together,
review official release notes/signing-key changes, pass CI scan/tests, and
back up the complete PVC and dedicated keyring Secret before rollout.

Proton's upstream license is GPL-3.0. Source and release history:
https://github.com/ProtonMail/proton-bridge/tree/v3.26.0
