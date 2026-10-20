"""Local dashboard: scan a mailbox for ads meant for someone else.

Run:  python app.py          then open http://127.0.0.1:5055
      python app.py --demo   to try it with sample emails (no account needed)
"""
from __future__ import annotations

import json
import sys
import threading
from collections import Counter
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from detector import Profile, analyze
from imap_client import MailClient, MailError

HERE = Path(__file__).parent
CONFIG_FILE = HERE / "config.json"
PORT = 5055
DEMO = "--demo" in sys.argv

app = Flask(__name__, static_folder=None)
state_lock = threading.Lock()
state: dict = {"folder": "", "uidvalidity": "", "raw": {}, "results": []}
_auth = None


def load_config() -> dict:
    if DEMO:
        from demo import DEMO_CONFIG
        return DEMO_CONFIG
    if not CONFIG_FILE.exists():
        raise MailError("config.json not found - copy config.example.json to config.json and fill it in.")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))


def save_config(cfg: dict) -> None:
    if DEMO:
        return  # demo config lives in memory only
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def profile_from(cfg: dict) -> Profile:
    return Profile(
        my_addresses=cfg.get("my_addresses") or [cfg["username"]],
        my_names=cfg.get("my_names", []),
        known_service_domains=cfg.get("known_service_domains", []),
    )


def outlook_auth(cfg: dict):
    global _auth
    if _auth is None:
        from outlook_auth import OutlookAuth  # only needed for Outlook accounts
        _auth = OutlookAuth(cfg.get("outlook_client_id", ""),
                            cfg.get("outlook_authority", "https://login.microsoftonline.com/consumers"))
    return _auth


def client(cfg: dict) -> MailClient:
    if DEMO:
        from demo import DemoClient
        return DemoClient()  # type: ignore[return-value]
    token = outlook_auth(cfg).token if cfg.get("auth") == "outlook_oauth" else None
    return MailClient(cfg, token)


def summarize(results: list[dict]) -> dict:
    flagged = [r for r in results if r["level"] != "clean"]
    domains = Counter(r["sender_domain"] for r in flagged if r["sender_domain"])
    names = Counter(n for r in flagged for n in r["names_seen"])
    return {
        "scanned": len(results),
        "likely": sum(r["level"] == "likely" for r in results),
        "possible": sum(r["level"] == "possible" for r in results),
        "top_domains": domains.most_common(15),
        "top_names": names.most_common(10),
    }


# -- local-only guards ---------------------------------------------------------
@app.before_request
def local_only():
    # Blocks DNS-rebinding: only answer requests addressed to this machine.
    if request.host not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}:
        abort(403)
    # Blocks cross-site form posts: JSON content type forces a CORS preflight we never approve.
    if request.method == "POST" and not request.is_json:
        abort(415)


@app.errorhandler(MailError)
@app.errorhandler(RuntimeError)
@app.errorhandler(ValueError)
def handle_error(e):
    return jsonify(error=str(e)), 400


# -- routes ------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(HERE / "static", "index.html")


@app.get("/api/status")
def status():
    try:
        cfg = load_config()
    except MailError as e:
        return jsonify(configured=False, error=str(e))
    info = {
        "configured": True,
        "username": cfg.get("username"),
        "auth": cfg.get("auth", "password"),
        "my_names": cfg.get("my_names", []),
        "known_service_domains": cfg.get("known_service_domains", []),
        "scan_folder": cfg.get("scan_folder", "INBOX"),
        "target_folder": cfg.get("target_folder", "Not Me"),
    }
    if info["auth"] == "outlook_oauth":
        a = outlook_auth(cfg)
        info.update(signed_in=a.signed_in_as(), login_pending=bool(a.pending), login_error=a.error)
        if a.pending:
            info.update(user_code=a.pending["user_code"], verification_uri=a.pending["verification_uri"])
    return jsonify(info)


@app.post("/api/login")
def login():
    return jsonify(outlook_auth(load_config()).start_device_flow())


@app.post("/api/logout")
def logout():
    outlook_auth(load_config()).sign_out()
    return jsonify(ok=True)


@app.post("/api/scan")
def scan():
    body = request.get_json()
    days = max(1, min(int(body.get("days", 60)), 3650))
    limit = max(1, min(int(body.get("limit", 300)), 2000))
    cfg = load_config()
    folder = cfg.get("scan_folder", "INBOX")
    profile = profile_from(cfg)
    raw, results = {}, []
    with client(cfg) as mc:
        uidvalidity, messages = mc.fetch_recent(folder, days, limit)
        for uid, data in messages:
            raw[uid] = data
            results.append(analyze(uid, data, profile).to_dict())
    with state_lock:
        state.update(folder=folder, uidvalidity=uidvalidity, raw=raw, results=results)
    return jsonify(results=results, summary=summarize(results))


@app.post("/api/known-service")
def add_known_service():
    """'This sender is mine' - stop flagging its account/order emails, then re-score."""
    domain = str(request.get_json().get("domain", "")).strip().lower()
    if not domain or "." not in domain:
        raise ValueError("Invalid domain.")
    cfg = load_config()
    known = cfg.setdefault("known_service_domains", [])
    if domain not in known:
        known.append(domain)
        save_config(cfg)
    profile = profile_from(cfg)
    with state_lock:
        state["results"] = [analyze(uid, data, profile).to_dict() for uid, data in state["raw"].items()]
        results = state["results"]
    return jsonify(results=results, summary=summarize(results))


@app.post("/api/move")
def move():
    uids = [str(u) for u in request.get_json().get("uids", [])]
    cfg = load_config()
    with state_lock:
        folder, uidvalidity = state["folder"], state["uidvalidity"]
        scanned = set(state["raw"])
    if not folder:
        raise MailError("Scan first.")
    if not set(uids) <= scanned:
        raise MailError("Some messages aren't from the last scan. Please re-scan.")
    with client(cfg) as mc:
        message = mc.move(folder, uids, cfg.get("target_folder", "Not Me"), uidvalidity)
    with state_lock:
        moved = set(uids)
        for u in moved:
            state["raw"].pop(u, None)
        state["results"] = [r for r in state["results"] if r["uid"] not in moved]
        results = state["results"]
    return jsonify(message=message, results=results, summary=summarize(results))


if __name__ == "__main__":
    print(f"Open http://127.0.0.1:{PORT}" + ("  (demo mode - sample emails)" if DEMO else ""))
    app.run(host="127.0.0.1", port=PORT, debug=False)
