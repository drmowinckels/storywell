"""Regenerate the self-hosted brand fonts for the desktop app.

Fetches the brand faces (Fraunces / Newsreader / IBM Plex Mono — the same as the
website) from the Google Fonts API, keeps only the ``latin`` + ``latin-ext`` subsets,
downloads the woff2 into ``src/storywell/desktop/web/fonts/`` and writes a local
``fonts.css``. Run from the repo root:

    python tools/bundle_fonts.py

Bundling (vs a CDN <link>) keeps the packaged app from calling Google on launch — a
privacy/GDPR concern for a desktop app — and lets it render offline. All three fonts are
SIL Open Font License 1.1; see web/fonts/LICENSE.txt.
"""

from __future__ import annotations

import pathlib
import re
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
CSS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Fraunces:opsz,wght@9..144,600&"
    "family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&"
    "family=IBM+Plex+Mono:wght@400;500&display=swap"
)
KEEP_SUBSETS = {"latin", "latin-ext"}
FONTS_DIR = pathlib.Path("src/storywell/desktop/web/fonts")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(request).read()


def main() -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    css = fetch(CSS_URL).decode()
    faces = []
    for block in re.split(r"(?=/\*)", css):
        subset = re.match(r"/\*\s*([\w-]+)\s*\*/", block.strip())
        if not subset or subset.group(1) not in KEEP_SUBSETS:
            continue
        family = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        style = re.search(r"font-style:\s*(\w+)", block).group(1)
        weight = re.search(r"font-weight:\s*([\d ]+)", block).group(1).strip().replace(" ", "-")
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
        slug = f"{family.lower().replace(' ', '')}-{weight}-{style}-{subset.group(1)}.woff2"
        (FONTS_DIR / slug).write_bytes(fetch(url))
        faces.append(block.replace(url, f"./{slug}").strip())
        print(f"  {slug}")

    header = (
        "/* Self-hosted brand fonts (SIL Open Font License) — see LICENSE.txt. Generated\n"
        "   from Google Fonts (latin + latin-ext only; rarer scripts fall back to the\n"
        "   system serif/mono). Regenerate with tools/bundle_fonts.py. */\n\n"
    )
    (FONTS_DIR / "fonts.css").write_text(header + "\n\n".join(faces) + "\n")
    print(f"wrote {len(faces)} faces + fonts.css")


if __name__ == "__main__":
    main()
