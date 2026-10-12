"""The profile's built-in extension: saved logins for the sites the profile is
used on.

Chromium reads an unpacked extension once, at launch, so the extension is
written per profile right before its container starts: a static content script
plus the profile's logins baked into `logins.js` and into the manifest's site
patterns. The content script runs only on those sites; secrets never reach any
other page."""

import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from app.store import Login

CONTENT_SCRIPT = Path(__file__).parent / "content.js"


def write(directory: Path, logins: list[Login]) -> None:
    """Replace `directory` with the extension for `logins`. A login's site is
    its URL's host without `www.`, subdomains included: a sign-in page often
    lives on another subdomain than the one the profile starts on."""
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    entries = []
    patterns: list[str] = []
    for login in logins:
        host = (urlsplit(login.url).hostname or "").removeprefix("www.")
        entries.append(
            {
                "host": host,
                "username": login.username,
                "password": login.password,
                "totp_secret": login.totp_secret,
            }
        )
        for pattern in (f"*://{host}/*", f"*://*.{host}/*"):
            if pattern not in patterns:
                patterns.append(pattern)
    manifest = {
        "manifest_version": 3,
        "name": "Saved logins",
        "version": "1.0",
        "description": "Fills this profile's sign-in forms and shows their codes.",
        "content_scripts": [
            {
                "matches": patterns,
                "js": ["logins.js", "content.js"],
                "run_at": "document_idle",
            }
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (directory / "logins.js").write_text(
        f"globalThis.SAVED_LOGINS = {json.dumps(entries)};\n"
    )
    shutil.copy(CONTENT_SCRIPT, directory / "content.js")
