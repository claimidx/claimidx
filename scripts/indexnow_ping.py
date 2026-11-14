"""Ping IndexNow for the public claimidx.com URL list.

    python scripts/indexnow_ping.py

The key file is public by design (https://www.indexnow.org). A failed ping must
not fail a production deploy; deploy-site treats a non-zero exit as a warning.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = "f32c1070cb225f8ac93a6cdc59b506c7"
KEY_URL = f"https://claimidx.com/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
HOST = "claimidx.com"


def _locs() -> list[str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_docs

    return list(sync_docs.sitemap_locs())


def main() -> int:
    urls = _locs()
    payload = {
        "host": HOST,
        "key": KEY,
        "keyLocation": KEY_URL,
        "urlList": urls,
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "claimidx-indexnow/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"IndexNow {resp.status} for {len(urls)} URLs")
            return 0
    except urllib.error.HTTPError as exc:
        print(f"IndexNow HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"IndexNow failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
