#!/usr/bin/env python3
"""Verify recovery of pass using only the dedicated exported key and PVC state."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('mail_runtime', HERE / 'runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class KeyringTests(unittest.TestCase):
    def test_import_trust_restore_and_wrong_key_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            keys = root / 'keyring'
            keys.mkdir()
            generate = root / 'generate'
            generate.mkdir(mode=0o700)
            agent = os.environ.get('TEST_GPG_AGENT')

            def agent_config(home):
                home.mkdir(mode=0o700, parents=True, exist_ok=True)
                if agent:
                    (home / 'gpg.conf').write_text(f'agent-program {agent}\n')

            agent_config(generate)
            env = dict(os.environ, GNUPGHOME=str(generate))
            subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                            '--quick-generate-key', 'Disposable Bridge test', 'rsa2048', 'encr', '0'],
                           env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            listing = subprocess.check_output(['gpg', '--batch', '--with-colons', '--list-secret-keys'],
                                              env=env, stderr=subprocess.DEVNULL).decode()
            fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
            (keys / 'fingerprint').write_text(fingerprint)
            (keys / 'gpg-private.asc').write_bytes(subprocess.check_output(
                ['gpg', '--batch', '--armor', '--export-secret-keys'], env=env))
            imported = root / 'imported'
            agent_config(imported)
            data = root / 'data'
            data.mkdir()
            with patch.object(runtime, 'DATA', data), patch.dict(os.environ, {
                    'GNUPGHOME': str(imported), 'PASSWORD_STORE_DIR': str(data / 'password-store'),
                    'KEYRING_DIR': str(keys)}):
                runtime.keyring()
                subprocess.run(['pass', 'insert', '-m', 'persisted-test'], input=b'test-session\n',
                               check=True, stdout=subprocess.DEVNULL)
                # A fresh ephemeral GPG home must recover the same encrypted pass record.
                restored = root / 'restored'
                agent_config(restored)
                with patch.dict(os.environ, {'GNUPGHOME': str(restored)}):
                    runtime.keyring()
                    self.assertEqual(subprocess.check_output(['pass', 'show', 'persisted-test']), b'test-session\n')
                (data / 'password-store/.gpg-id').write_text('A' * 40)
                with self.assertRaises(ValueError):
                    runtime.keyring()
            # Agents exit when their private socket directories disappear; no real
            # user keyring or credentials were accessed by this test.


if __name__ == '__main__':
    unittest.main()
