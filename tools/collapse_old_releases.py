"""Folds the notes of every older release on the GitHub releases page.

The newest release keeps its notes open. Every other one gets a line pointing to the
latest release, and its "New in …" sections and its downloads fold into <details>
blocks, so the page stays short. Nothing is deleted: every word stays in the folded
blocks, and the files of the releases are not touched.

    python tools/collapse_old_releases.py <current tag>            folds via the GitHub API (GITHUB_TOKEN)
    python tools/collapse_old_releases.py <current tag> --dry-run  prints what would change
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "Bratwurstexe/FPV_DVR_CLEANER")
API = f"https://api.github.com/repos/{REPOSITORY}"
MARKER = "<!-- folded: older release -->"


def fold(body: str, tag: str) -> str:
    """The notes of an older release with its sections folded; folded notes stay as they are."""
    if MARKER in body:
        return body
    text = body.replace("\r\n", "\n").strip()
    # The parts before the first heading (the launcher box) and every "## …" section.
    pieces = re.split(r"(?m)^(?=## )", text)
    news, rest = [], []
    for piece in pieces:
        heading = piece.splitlines()[0] if piece.startswith("## ") else ""
        (news if re.match(r"## New\b", heading) else rest).append(piece.strip())
    blocks = [f"> This is an older version. The newest FPV DVR Cleaner is the "
              f"**[latest release](https://github.com/{REPOSITORY}/releases/latest)**."]
    for piece in news:
        heading, _, content = piece.partition("\n")
        blocks.append(f"<details>\n<summary><b>{heading[3:].strip()}</b></summary>\n\n{content.strip()}\n\n</details>")
    if rest:
        blocks.append(f"<details>\n<summary>Download {tag} and first start</summary>\n\n"
                      + "\n\n".join(piece for piece in rest if piece) + "\n\n</details>")
    blocks.append(MARKER)
    return "\n\n".join(blocks) + "\n"


def request(method: str, url: str, token: str, data: dict | None = None):
    payload = json.dumps(data).encode() if data is not None else None
    call = urllib.request.Request(url, data=payload, method=method, headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    with urllib.request.urlopen(call, timeout=60) as response:
        return json.load(response)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    current, dry_run = sys.argv[1], "--dry-run" in sys.argv
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token and not dry_run:
        print("GITHUB_TOKEN is not set")
        return 2
    if token:
        releases = request("GET", f"{API}/releases?per_page=100", token)
    else:
        with urllib.request.urlopen(f"{API}/releases?per_page=100", timeout=60) as response:
            releases = json.load(response)
    for release in releases:
        if release["tag_name"] == current or release.get("draft"):
            continue
        body = release.get("body") or ""
        folded = fold(body, release["tag_name"])
        if folded == body:
            print(f"{release['tag_name']}: already folded")
            continue
        if dry_run:
            print(f"===== {release['tag_name']}\n{folded}")
            continue
        # Only the text changes; name, tag, files and the "latest" mark stay as they are.
        request("PATCH", f"{API}/releases/{release['id']}", token, {"body": folded})
        print(f"{release['tag_name']}: folded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
