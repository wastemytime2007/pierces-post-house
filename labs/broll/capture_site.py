"""Capture pages of a website as still images for the B-roll pool (headless Google Chrome; nothing is installed or logged into).

    python3 labs/broll/capture_site.py --url https://soldfast.com --out "<folder>" [--max 10] [--width 1440 --height 1800]

The pages are the ones in the site's own menu (links whose text is one of the usual menu words), plus the home page. Each becomes `<name>.png` and
`captures.json` records the address, the time it was captured and the HTTP status, so a still can always be traced back to a live page.

Limits, said plainly: stills only (no scrolling recordings yet); what the page shows today is what is captured, including a chat bubble, cookie
banners or a customer's name and quote when the page carries them, so a capture is a candidate to look at before it goes in a video, never placed blindly."""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MENU_WORDS = {"sell your house", "how it works", "compare", "faq", "reviews", "about us", "our company", "blog", "press", "services", "contact us", "get a cash offer today!"}


class CaptureError(Exception):
    pass


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PostHouse B-roll capture)"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError) as e:
        raise CaptureError(f"could not reach {url} ({e})")


def menu_pages(base: str, page: str, limit: int) -> list[tuple[str, str]]:
    """[(name, absolute url)] for the home page and every anchor whose visible text is a menu word, in page order, no repeats."""
    out = [("home", base.rstrip("/") + "/")]
    seen = {out[0][1]}
    for href, inner in re.findall(r'<a\b[^>]*?href="([^"#][^"]*)"[^>]*>(.*?)</a>', page, re.S | re.I):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip().lower().rstrip("›»> ").strip()
        if text not in MENU_WORDS:
            continue
        url = urllib.parse.urljoin(base, href)
        if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(base).netloc:
            continue
        url = url.split("?")[0]
        if not url.endswith("/") and "." not in url.rsplit("/", 1)[-1]:
            url += "/"
        if url in seen:
            continue
        seen.add(url)
        out.append((re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "page", url))
        if len(out) >= limit:
            break
    return out


def shoot(url: str, dst: Path, width: int, height: int) -> None:
    if not Path(CHROME).exists():
        raise CaptureError("Google Chrome is not at its usual place, so pages cannot be captured")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={width},{height}", "--virtual-time-budget=8000",
                    f"--screenshot={dst}", url], capture_output=True, timeout=90)
    if not dst.exists() or dst.stat().st_size < 5000:
        raise CaptureError(f"no usable screenshot of {url}")


def capture(url: str, out: Path, limit: int = 10, width: int = 1440, height: int = 1800) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    status, page = fetch(url)
    if status != 200:
        raise CaptureError(f"{url} answered {status}")
    rows = []
    for name, u in menu_pages(url, page, limit):
        dst = out / f"{name}.png"
        try:
            shoot(u, dst, width, height)
            st = fetch(u)[0]
            rows.append({"name": name, "url": u, "file": dst.name, "http_status": st, "captured": datetime.datetime.now().isoformat(timespec="seconds")})
            print(f"  {name}: {u}", flush=True)
        except CaptureError as e:
            print(f"  skipped {u}: {e}", file=sys.stderr)
    (out / "captures.json").write_text(json.dumps(rows, indent=1))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=1800)
    a = ap.parse_args(argv)
    try:
        rows = capture(a.url, Path(a.out).expanduser(), a.max, a.width, a.height)
    except CaptureError as e:
        print(f"capture: {e}", file=sys.stderr)
        return 1
    print(f"{len(rows)} page(s) captured into {a.out}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
