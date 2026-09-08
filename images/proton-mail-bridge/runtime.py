#!/usr/bin/env python3
"""Lifecycle helpers; all mailbox authentication remains in official Bridge."""
import fcntl
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import subprocess
import sys
import time

BRIDGE = os.environ.get('BRIDGE_BINARY', '/usr/local/lib/proton-bridge/bridge')
HAPROXY = os.environ.get('HAPROXY_BINARY', '/usr/sbin/haproxy')
DATA = Path(os.environ.get('HOME', '/data'))
RUN = Path(os.environ.get('BRIDGE_RUN', '/run/bridge'))
PROXY_RUN = Path(os.environ.get('PROXY_RUN', '/run/proxy'))
TLS = Path(os.environ.get('TLS_DIR', '/tls'))
PUBLIC = Path(os.environ.get('BRIDGE_PUBLIC', '/bridge-public'))
MODE = os.environ.get('BRIDGE_MODE', 'setup')
stopping = False


def stop(_signum, _frame):
    global stopping
    stopping = True


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def initialize():
    for name in ('config', 'share', 'cache', 'password-store', 'tls-export', 'public'):
        (DATA / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    (DATA / 'public').chmod(0o750)


def keyring():
    """Import only a dedicated key; never silently replace an existing pass key."""
    initialize()
    home = Path(os.environ['GNUPGHOME'])
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    secrets = Path(os.environ.get('KEYRING_DIR', '/keyring'))
    secret = secrets / 'gpg-private.asc'
    fingerprint = (secrets / 'fingerprint').read_text().strip()
    if not re.fullmatch(r'[A-F0-9]{40}', fingerprint):
        raise ValueError('Invalid dedicated GPG fingerprint')
    run('gpg', '--batch', '--import', str(secret), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run('gpg', '--batch', '--import-ownertrust', input=(fingerprint + ':6:\n').encode(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key_id = Path(os.environ['PASSWORD_STORE_DIR']) / '.gpg-id'
    if key_id.exists() and key_id.read_text().strip() != fingerprint:
        raise ValueError('Key differs from persisted password store; restore the matching Secret')
    if not key_id.exists():
        run('pass', 'init', fingerprint, stdout=subprocess.DEVNULL)
    # Prove unattended decryption works before Bridge can fall back to an insecure vault.
    probe = os.urandom(32)
    encrypted = run('gpg', '--batch', '--trust-model', 'always', '--recipient', fingerprint,
                    '--encrypt', input=probe, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    decrypted = run('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                    '--decrypt', input=encrypted, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    if decrypted != probe:
        raise ValueError('Keyring round-trip failed')
    # Exercise pass itself too: Bridge's helper needs GPG ownertrust, not just
    # an encryption command forced to trust a recipient.
    marker = probe.hex().encode() + b'\n'
    run('pass', 'insert', '--multiline', '--force', 'runtime-keyring-check',
        input=marker, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stored = run('pass', 'show', 'runtime-keyring-check', stdout=subprocess.PIPE,
                 stderr=subprocess.DEVNULL).stdout
    run('pass', 'rm', '--force', 'runtime-keyring-check', stdout=subprocess.DEVNULL)
    if stored != marker:
        raise ValueError('Password store round-trip failed')


def instance_lock():
    RUN.mkdir(parents=True, exist_ok=True)
    lock = (RUN / 'instance.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        sys.exit('Bridge already owns the vault. Switch to setup mode through Git before opening the CLI.')
    return lock


def bridge():
    RUN.mkdir(parents=True, exist_ok=True)
    child = None
    lock = None
    if MODE == 'run':
        lock = instance_lock()
        keyring()
        child = subprocess.Popen([BRIDGE, '--noninteractive', '--log-level', 'warn'])
    elif MODE != 'setup':
        raise ValueError('BRIDGE_MODE must be setup or run')
    print(f'Bridge supervisor: {MODE} mode', flush=True)
    try:
        while not stopping:
            if child is not None and child.poll() is not None:
                raise RuntimeError('Bridge exited; Kubernetes will restart the container')
            (RUN / 'heartbeat').touch()
            time.sleep(1)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if lock:
            lock.close()


def cli():
    if MODE != 'setup':
        sys.exit('Set BRIDGE_MODE=setup through Git first; the proxy must close mail access during setup.')
    with instance_lock():
        keyring()
        # Attached terminal only: login/info output never enters container stdout logs.
        return subprocess.call([BRIDGE, '--cli', '--log-level', 'error'])


def publish_cert():
    if MODE != 'setup':
        sys.exit('Publish the backend trust certificate in setup mode')
    with instance_lock():
        cert = DATA / 'tls-export/cert.pem'
        # Exported private key stays in the private data directory, never the proxy mount.
        ssl.create_default_context(cafile=str(cert))
        target = DATA / 'public/bridge-ca.pem'
        temp = target.with_suffix('.tmp')
        temp.write_bytes(cert.read_bytes())
        temp.chmod(0o640)
        temp.replace(target)
        print('Published public Bridge TLS certificate; no private key was copied.')


def networks(value):
    return ' '.join(str(ipaddress.ip_network(x.strip())) for x in value.split(',') if x.strip())


def proxy_config(mail, pem_path, ca_path):
    trusted = networks(os.environ.get('TRUSTED_CIDRS', '192.168.25.0/24,192.168.30.0/24'))
    checks = networks(os.environ.get('ACME_CIDRS', '192.168.10.21/32,10.42.0.0/16'))
    if not trusted or not checks:
        raise ValueError('Empty network allowlist')
    host = os.environ.get('MAIL_HOST', 'mail.terminus.home.arpa')
    if not re.fullmatch(r'[a-z0-9.-]+', host):
        raise ValueError('Invalid mail hostname')
    upstream = os.environ.get('ACME_UPSTREAM', 'traefik.traefik.svc.cluster.local:80')
    if not re.fullmatch(r'[a-zA-Z0-9.:-]+', upstream):
        raise ValueError('Invalid ACME upstream')
    config = f'''global
    log stdout format raw local0 warning
    maxconn 128
    hard-stop-after 1h
    ssl-default-bind-options ssl-min-ver TLSv1.2
    ssl-default-server-options ssl-min-ver TLSv1.2
defaults
    log global
    mode tcp
    timeout connect 5s
    timeout client 35m
    timeout server 35m
    timeout check 5s
resolvers cluster_dns
    parse-resolv-conf
    hold valid 10s
frontend health
    bind 127.0.0.1:18080
    mode http
    http-request return status 200 content-type text/plain string alive
frontend acme
    bind :10080
    mode http
    timeout client 10s
    acl allowed_source src {checks}
    acl allowed_host hdr(host) -i {host} {host}:80
    acl challenge path_reg ^/\\.well-known/acme-challenge/[A-Za-z0-9_-]+$
    acl get_method method GET
    http-request deny unless allowed_source allowed_host challenge get_method
    default_backend acme_solver
backend acme_solver
    mode http
    timeout server 10s
    server traefik {upstream} resolvers cluster_dns resolve-prefer ipv4 init-addr libc,none
'''
    if mail:
        for name, frontend, backend in [('imap', 1993, 1143), ('smtp', 1465, 1025)]:
            config += f'''frontend {name}
    bind :{frontend} ssl crt {pem_path}
    acl trusted src {trusted}
    tcp-request connection reject unless trusted
    default_backend bridge_{name}
backend bridge_{name}
    server bridge 127.0.0.1:{backend} ssl verify required ca-file {ca_path} verifyhost 127.0.0.1 check inter 10s fall 3 rise 2
'''
    return config


def proxy():
    PROXY_RUN.mkdir(parents=True, exist_ok=True)
    config = PROXY_RUN / 'haproxy.cfg'
    pem = PROXY_RUN / 'mail.pem'
    ca = PROXY_RUN / 'bridge-ca.pem'
    previous = None
    child = None
    try:
        while not stopping:
            if child is not None and child.poll() is not None:
                raise RuntimeError('HAProxy exited; Kubernetes will restart the container')
            material = b''
            backend_ca = b''
            if MODE == 'run':
                try:
                    cert, key = TLS / 'tls.crt', TLS / 'tls.key'
                    # A projected Secret can change between reads; mismatches fail closed.
                    material = cert.read_bytes() + b'\n' + key.read_bytes()
                    candidate = PROXY_RUN / 'candidate.pem'
                    candidate.write_bytes(material)
                    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(candidate)
                    run('openssl', 'x509', '-in', str(candidate), '-checkend', '0',
                        '-noout', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    backend_ca = (PUBLIC / 'bridge-ca.pem').read_bytes()
                    ssl.create_default_context(cadata=backend_ca.decode())
                except (OSError, ValueError, ssl.SSLError, subprocess.CalledProcessError):
                    material = backend_ca = b''
            digest = hashlib.sha256(material + backend_ca).hexdigest()
            if digest != previous:
                enabled = bool(material and backend_ca)
                if enabled:
                    pem.write_bytes(material)
                    ca.write_bytes(backend_ca)
                candidate_config = PROXY_RUN / 'candidate.cfg'
                candidate_config.write_text(proxy_config(enabled, pem, ca))
                run(HAPROXY, '-c', '-f', str(candidate_config), stdout=subprocess.DEVNULL)
                candidate_config.replace(config)
                if child is None:
                    child = subprocess.Popen([HAPROXY, '-W', '-db', '-f', str(config)])
                else:
                    child.send_signal(signal.SIGUSR2)
                previous = digest
                print('HAProxy configuration loaded: ' + ('mail TLS enabled' if enabled else 'ACME only'), flush=True)
            (PROXY_RUN / 'heartbeat').touch()
            time.sleep(2)
    finally:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGUSR1)
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)


def health(component, kind):
    directory = RUN if component == 'bridge' else PROXY_RUN
    if time.time() - (directory / 'heartbeat').stat().st_mtime > 15:
        return 1
    # Liveness has no dependency on Proton availability or successful account login.
    if kind == 'live':
        return 0
    if component == 'proxy':
        with socket.create_connection(('127.0.0.1', 18080), timeout=3):
            pass
        if MODE == 'run':
            for port in (1993, 1465):
                with socket.create_connection(('127.0.0.1', port), timeout=3):
                    pass
    elif MODE == 'run':
        context = ssl.create_default_context(cafile=str(DATA / 'public/bridge-ca.pem'))
        for port, greeting in ((1143, b'* OK'), (1025, b'220')):
            with socket.create_connection(('127.0.0.1', port), timeout=3) as conn:
                with context.wrap_socket(conn, server_hostname='127.0.0.1') as tls:
                    if not tls.recv(1024).startswith(greeting):
                        return 1
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        action = sys.argv[1]
        if action == 'health':
            sys.exit(health(*sys.argv[2:]))
        sys.exit({'init': initialize, 'bridge': bridge, 'cli': cli,
                  'publish-cert': publish_cert, 'proxy': proxy}[action]())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # No exception arguments: a failing subprocess must not disclose sensitive input.
        print(f'Mail runtime failed ({type(exc).__name__}); inspect configuration and local health.', file=sys.stderr)
        sys.exit(1)
