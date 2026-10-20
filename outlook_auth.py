"""Microsoft sign-in (device code flow) for Outlook.com / Hotmail IMAP.

Outlook.com no longer accepts plain passwords over IMAP, so we get an OAuth
token with the IMAP.AccessAsUser.All scope and use it via XOAUTH2.
"""
from __future__ import annotations

import threading
from pathlib import Path

import msal

SCOPES = ["https://outlook.office.com/IMAP.AccessAsUser.All"]
CACHE_FILE = Path(__file__).with_name(".token_cache.json")


class OutlookAuth:
    def __init__(self, client_id: str, authority: str = "https://login.microsoftonline.com/consumers"):
        if not client_id:
            raise ValueError("outlook_client_id is missing in config.json - see README.")
        self.cache = msal.SerializableTokenCache()
        if CACHE_FILE.exists():
            self.cache.deserialize(CACHE_FILE.read_text())
        self.app = msal.PublicClientApplication(client_id, authority=authority, token_cache=self.cache)
        self.lock = threading.Lock()
        self.pending: dict | None = None  # device flow currently waiting for the user
        self.error: str = ""

    def _save(self) -> None:
        if self.cache.has_state_changed:
            CACHE_FILE.write_text(self.cache.serialize())

    def token(self) -> str:
        """Returns a valid access token, refreshing silently. Raises if not signed in."""
        with self.lock:
            for account in self.app.get_accounts():
                result = self.app.acquire_token_silent(SCOPES, account=account)
                if result and "access_token" in result:
                    self._save()
                    return result["access_token"]
        raise RuntimeError("Not signed in to Microsoft yet.")

    def signed_in_as(self) -> str:
        accounts = self.app.get_accounts()
        return accounts[0].get("username", "") if accounts else ""

    def start_device_flow(self) -> dict:
        """Starts sign-in; returns code + URL for the user. Completion happens in a thread."""
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(flow.get("error_description", "Could not start Microsoft sign-in."))
        self.pending, self.error = flow, ""

        def wait() -> None:
            result = self.app.acquire_token_by_device_flow(flow)  # blocks until done/expired
            with self.lock:
                if "access_token" in result:
                    self._save()
                else:
                    self.error = result.get("error_description", "Sign-in failed.")
                self.pending = None

        threading.Thread(target=wait, daemon=True).start()
        return {"user_code": flow["user_code"], "verification_uri": flow["verification_uri"]}

    def sign_out(self) -> None:
        for account in self.app.get_accounts():
            self.app.remove_account(account)
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
