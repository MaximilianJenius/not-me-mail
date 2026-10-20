"""IMAP access: read recent mail without marking it read, and move chosen UIDs."""
from __future__ import annotations

import imaplib
import os
import re
from datetime import date, timedelta
from typing import Callable, Iterator

FETCH_BYTES = 80_000  # first 80 KB of each message is plenty for headers + greeting
BATCH = 25


class MailError(Exception):
    pass


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MailClient:
    def __init__(self, cfg: dict, oauth_token: Callable[[], str] | None = None):
        self.cfg = cfg
        self.oauth_token = oauth_token
        self.conn: imaplib.IMAP4_SSL | None = None

    # -- connection -------------------------------------------------------
    def __enter__(self) -> "MailClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
            self.conn = None

    def connect(self) -> None:
        host, port = self.cfg["imap_host"], int(self.cfg.get("imap_port", 993))
        user = self.cfg["username"]
        try:
            self.conn = imaplib.IMAP4_SSL(host, port, timeout=60)
            if self.cfg.get("auth", "password") == "outlook_oauth":
                if self.oauth_token is None:
                    raise MailError("Not signed in to Microsoft yet.")
                token = self.oauth_token()
                auth_string = f"user={user}\x01auth=Bearer {token}\x01\x01".encode()
                self.conn.authenticate("XOAUTH2", lambda _: auth_string)
            else:
                password = os.environ.get("IMAP_PASSWORD")
                if not password:
                    raise MailError("Set the IMAP_PASSWORD environment variable (use an app password).")
                self.conn.login(user, password)
        except imaplib.IMAP4.error as e:
            raise MailError(f"IMAP login failed: {e}") from e
        except OSError as e:
            raise MailError(f"Could not reach {host}:{port}: {e}") from e

    def _ok(self, typ: str, data, what: str):
        if typ != "OK":
            detail = data[0].decode(errors="replace") if data and isinstance(data[0], bytes) else data
            raise MailError(f"{what} failed: {detail}")
        return data

    def _select(self, folder: str, readonly: bool) -> str:
        """Selects folder and returns its UIDVALIDITY."""
        assert self.conn
        self._ok(*self.conn.select(_quote(folder), readonly=readonly), f"Opening {folder}")
        _, data = self.conn.response("UIDVALIDITY")
        return data[0].decode() if data and data[0] else ""

    def list_folders(self) -> list[str]:
        assert self.conn
        data = self._ok(*self.conn.list(), "Listing folders")
        folders = []
        for line in data:
            if not line:
                continue
            m = re.match(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
            if m and b"\\Noselect" not in m.group("flags"):
                folders.append(m.group("name").decode().strip('"'))
        return folders

    # -- reading ----------------------------------------------------------
    def fetch_recent(self, folder: str, days: int, limit: int) -> tuple[str, Iterator[tuple[str, bytes]]]:
        """Returns (uidvalidity, iterator of (uid, raw_bytes)) for newest `limit` mails in last `days`."""
        assert self.conn
        uidvalidity = self._select(folder, readonly=True)
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        data = self._ok(*self.conn.uid("SEARCH", None, "SINCE", since), "Searching")
        uids = data[0].split()[-limit:] if data and data[0] else []
        uids.reverse()  # newest first

        def gen() -> Iterator[tuple[str, bytes]]:
            assert self.conn
            for i in range(0, len(uids), BATCH):
                chunk = b",".join(uids[i:i + BATCH]).decode()
                resp = self._ok(*self.conn.uid("FETCH", chunk, f"(UID BODY.PEEK[]<0.{FETCH_BYTES}>)"), "Fetching")
                for item in resp:
                    if isinstance(item, tuple):
                        m = re.search(rb"UID (\d+)", item[0])
                        if m:
                            yield m.group(1).decode(), item[1]

        return uidvalidity, gen()

    # -- moving -----------------------------------------------------------
    def move(self, folder: str, uids: list[str], target: str, expected_uidvalidity: str) -> str:
        """Moves UIDs from folder to target. Returns a human-readable summary."""
        assert self.conn
        if not uids:
            return "Nothing to move."
        if not all(u.isdigit() for u in uids):
            raise MailError("Invalid message id.")
        uidvalidity = self._select(folder, readonly=False)
        if expected_uidvalidity and uidvalidity != expected_uidvalidity:
            raise MailError("The mailbox changed since the scan (UIDVALIDITY differs). Please re-scan.")

        typ, data = self.conn.create(_quote(target))
        if typ != "OK" and b"exist" not in (data[0] or b"").lower():
            # Some servers say [ALREADYEXISTS]; anything else is a real failure.
            if b"ALREADYEXISTS" not in (data[0] or b""):
                raise MailError(f"Could not create folder {target}: {data[0]!r}")

        uidset = ",".join(uids)
        caps = {c.decode().upper() for c in self.conn.capabilities}
        if "MOVE" in caps:
            self._ok(*self.conn.uid("MOVE", uidset, _quote(target)), "Moving")
            return f"Moved {len(uids)} message(s) to '{target}'."

        self._ok(*self.conn.uid("COPY", uidset, _quote(target)), "Copying")
        self._ok(*self.conn.uid("STORE", uidset, "+FLAGS.SILENT", r"(\Deleted)"), "Flagging")
        if "UIDPLUS" in caps:
            # UID EXPUNGE only removes these exact messages, never others you had flagged.
            self._ok(*self.conn.uid("EXPUNGE", uidset), "Expunging")
            return f"Moved {len(uids)} message(s) to '{target}'."
        return (f"Copied {len(uids)} message(s) to '{target}' and marked the originals deleted "
                f"(server lacks MOVE/UIDPLUS, so they stay in {folder} until your client purges them).")
