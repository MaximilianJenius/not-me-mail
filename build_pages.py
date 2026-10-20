"""Builds docs/index.html: the dashboard with the demo emails pre-scored, for GitHub Pages.

Run:  python build_pages.py   (re-run after changing detector.py, demo.py or static/index.html)
"""
from __future__ import annotations

import json
from pathlib import Path

from demo import DEMO_CONFIG, SAMPLES
from detector import Profile, analyze

HERE = Path(__file__).parent
REPO_URL = "https://github.com/MaximilianJenius/not-me-mail"
PLACEHOLDER = "/*DEMO_DATA*/null"


def profile(extra_domain: str = "") -> Profile:
    known = DEMO_CONFIG["known_service_domains"] + ([extra_domain] if extra_domain else [])
    return Profile(DEMO_CONFIG["my_addresses"], DEMO_CONFIG["my_names"], known)


def main() -> None:
    messages = []
    for i, raw in enumerate(SAMPLES):
        uid = str(i + 1)
        base = analyze(uid, raw, profile())
        # "It's mine" only changes the score of mail from that same domain, so one
        # alternative verdict per message is enough for the page to re-score exactly.
        if_mine = analyze(uid, raw, profile(base.sender_domain))
        messages.append({"base": base.to_dict(), "if_mine": if_mine.to_dict()})

    data = {
        "repo_url": REPO_URL,
        "config": {k: DEMO_CONFIG[k] for k in ("username", "auth", "my_names",
                                                "known_service_domains", "scan_folder", "target_folder")},
        "messages": messages,
    }
    # Escape "</" so email text can never close the <script> tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    if html.count(PLACEHOLDER) != 1:
        raise SystemExit(f"Expected exactly one {PLACEHOLDER} in static/index.html")
    out = HERE / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html.replace(PLACEHOLDER, payload), encoding="utf-8")
    print(f"Wrote {out} ({len(messages)} demo emails)")


if __name__ == "__main__":
    main()
