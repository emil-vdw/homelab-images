from __future__ import annotations

import fcntl
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class StateError(RuntimeError):
    pass


class AlreadyRunning(StateError):
    pass


@dataclass(frozen=True)
class StoredObject:
    destination: str
    completed: bool


class Ledger:
    def __init__(self, path: str | Path, archive_root: str, account: str) -> None:
        self.path = Path(path)
        self.archive_root = archive_root.rstrip("/")
        self.account = account
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> "Ledger":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        try:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS objects (
                    archive_root TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                    PRIMARY KEY (archive_root, digest)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    archive_root TEXT NOT NULL,
                    account TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uidvalidity INTEGER NOT NULL,
                    uid INTEGER NOT NULL,
                    PRIMARY KEY (
                        archive_root, account, source_id, mailbox, uidvalidity, uid
                    )
                );
                """
            )
        except BaseException:
            self._connection.close()
            self._connection = None
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StateError("ledger is not open")
        return self._connection

    @contextmanager
    def locked(self) -> Iterator[None]:
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AlreadyRunning("another importer process holds the state lock") from error
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def message_completed(
        self,
        source_id: str,
        mailbox: str,
        uidvalidity: int,
        uid: int,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM messages
            WHERE archive_root = ? AND account = ? AND source_id = ?
              AND mailbox = ? AND uidvalidity = ? AND uid = ?
            """,
            (self.archive_root, self.account, source_id, mailbox, uidvalidity, uid),
        ).fetchone()
        return row is not None

    def reserve(self, digest: str, destination: str) -> StoredObject:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO objects (archive_root, digest, destination)
                VALUES (?, ?, ?)
                """,
                (self.archive_root, digest, destination),
            )
            row = self.connection.execute(
                """
                SELECT destination, completed FROM objects
                WHERE archive_root = ? AND digest = ?
                """,
                (self.archive_root, digest),
            ).fetchone()
        if row is None:
            raise StateError("object reservation disappeared")
        return StoredObject(destination=row[0], completed=bool(row[1]))

    def complete(self, digest: str) -> None:
        with self.connection:
            changed = self.connection.execute(
                """
                UPDATE objects SET completed = 1
                WHERE archive_root = ? AND digest = ?
                """,
                (self.archive_root, digest),
            ).rowcount
        if changed != 1:
            raise StateError("cannot complete an object without a reservation")

    def mark_message_complete(
        self,
        source_id: str,
        mailbox: str,
        uidvalidity: int,
        uid: int,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    archive_root, account, source_id, mailbox, uidvalidity, uid
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (self.archive_root, self.account, source_id, mailbox, uidvalidity, uid),
            )
