# Mail attachment importer

This image reads PDF attachments from configured IMAP folders and archives them through WebDAV. It leaves messages and flags untouched. A SQLite ledger keeps retries safe when a rule changes or an archived file is moved later.

The routing file is YAML:

```yaml
timezone: Europe/Amsterdam
sources:
  - id: invoices
    mailbox: Invoices
    unmatched_destination: Inbox/Unsorted invoices
    rules:
      - id: home-internet
        from_contains: billing@provider.example
        subject_contains: internet
        destination: Home/Utilities and internet/Internet/Invoices/{year}
```

Every condition in a rule must match. Rules run in order, and the first match chooses the destination. Templates support `{year}`, `{month}`, and `{date}` based on the email Date header. The importer falls back to the IMAP internal date when that header is missing or invalid.

The normal command is:

```sh
python -m mail_importer --config /config/rules.yaml --state /state/imports.sqlite3
```

`--check-config` needs no credentials. `--list-folders` needs only the IMAP settings. `--dry-run` connects to IMAP and prints filenames and planned destinations, but it does not connect to WebDAV or create state files. Treat dry-run output as private.

Connection settings come from the environment:

- `IMAP_HOST`, `IMAP_USERNAME`, and `IMAP_PASSWORD`
- `IMAP_PORT`, which defaults to `993`
- `IMAP_TLS_SERVER_NAME`, which defaults to `mail.terminus.home.arpa`
- `SSL_CERT_FILE`, when the internal CA is not in the system trust store
- `OPENCLOUD_WEBDAV_BASE_URL`, the HTTPS URL for the Documents share root
- `OPENCLOUD_USERNAME` and `OPENCLOUD_APP_TOKEN`

The WebDAV account must have access to the configured destinations. OpenCloud 7.4.0 ignores `If-None-Match` on PUT, so the importer uploads to its own hidden temporary name, verifies the bytes, and publishes it with `MOVE` and `Overwrite: F`. It reuses that deterministic temporary name after a crash and removes it when another copy already won. OpenCloud's storage backend checks for the destination before renaming, but that check and rename are not one atomic operation. A concurrent external writer in that narrow interval could still be replaced. Keep the job suspended until a bounded live upload and repeat run confirm the deployed server's behavior.

OpenCloud processes new uploads asynchronously and returns HTTP 425 until their bytes are available. The importer waits up to two minutes per verification read, honoring `Retry-After` when present. If that wait expires, it preserves the temporary upload and incomplete ledger entry for the next run.

Back up `imports.sqlite3` while no importer process is running. If the ledger is lost, suspend the job until it is restored or the resulting full rescan has been reviewed.
