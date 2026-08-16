"""Prove an *installed* odforge actually serves its web cockpit.

Run this against a venv that has the wheel installed — never against the source
tree, which is precisely the environment that used to hide the bug: the old
``frontend_dist()`` walked up from ``__file__`` to ``<repo>/web/dist``, so every
developer saw a working UI and every wheel user got a bare 404.

    python -m pip install "dist/odforge-*.whl[web]"
    python scripts/wheel_smoke.py

Exits non-zero with a specific reason on any failure, so CI can gate a release
on it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    from fastapi.testclient import TestClient

    from odforge.webapi import create_app, frontend_dist

    dist = frontend_dist()
    if dist is None:
        print("FAIL: this install has no web cockpit (frontend_dist() is None)")
        return 1
    print(f"frontend_dist() -> {dist}")

    # Guard against the failure mode this script exists for: resolving to a repo
    # checkout that happens to be nearby, rather than to the installed package.
    if "site-packages" not in str(dist) and "--allow-source-tree" not in sys.argv:
        print(
            f"FAIL: resolved to a source tree, not an install: {dist}\n"
            "      Run this against a venv with the wheel installed "
            "(or pass --allow-source-tree to check a dev build)."
        )
        return 1

    app = create_app()
    with TestClient(app) as client:
        index = client.get("/")
        print(f"GET / -> {index.status_code}")
        if index.status_code != 200:
            print("FAIL: the served index page is not 200")
            return 1

        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.text)
        if not any(a.endswith(".js") for a in assets):
            print("FAIL: index.html references no JS bundle")
            return 1
        if not any(a.endswith(".css") for a in assets):
            print("FAIL: index.html references no CSS bundle")
            return 1

        for asset in assets:
            response = client.get(asset)
            size = len(response.content)
            print(f"GET {asset} -> {response.status_code} ({size} bytes)")
            if response.status_code != 200 or size == 0:
                print(f"FAIL: asset {asset} is not served")
                return 1

    print("OK: wheel serves index + JS + CSS")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
